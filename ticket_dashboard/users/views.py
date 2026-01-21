import logging

import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from django.views.generic import RedirectView
from django.views.generic import TemplateView
from django.views.generic import UpdateView

from ticket_dashboard.services.eramba import ErambaService
from ticket_dashboard.services.espocrm import EspoService
from ticket_dashboard.services.gitlab import GitLabService
from ticket_dashboard.services.openproject import OpenProjectService

# Services
from ticket_dashboard.services.zammad import ZammadService
from django.http import HttpResponseRedirect
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from ticket_dashboard.users.models import ExternalGroup
from ticket_dashboard.users.models import SavedView
from ticket_dashboard.users.models import ServiceConfiguration
from ticket_dashboard.users.models import TicketPermission

# Models
from ticket_dashboard.users.models import User

logger = logging.getLogger(__name__)


# --- USER VIEWS ---
class UserDetailView(LoginRequiredMixin, DetailView):
    model = User
    slug_field = "id"
    slug_url_kwarg = "id"


user_detail_view = UserDetailView.as_view()


class UserUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = User
    fields = ["name"]
    success_message = _("Information successfully updated")

    def get_success_url(self) -> str:
        return self.request.user.get_absolute_url()

    def get_object(self, queryset: QuerySet | None = None) -> User:
        return self.request.user


user_update_view = UserUpdateView.as_view()


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self) -> str:
        return reverse("users:detail", kwargs={"pk": self.request.user.pk})


user_redirect_view = UserRedirectView.as_view()


# --- DASHBOARD VIEW ---
class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):  # noqa: C901, PLR0912, PLR0915
        context = super().get_context_data(**kwargs)
        request = self.request

        # 1. INITIALIZE LIST
        all_tickets = []
        force_refresh = request.GET.get("refresh") == "1"

        # 2. FETCH SERVICES DYNAMICALLY
        service_map = {
            "zammad": ZammadService,
            "gitlab": GitLabService,
            "espocrm": EspoService,
            "openproject": OpenProjectService,
            "eramba": ErambaService,
        }

        configs = ServiceConfiguration.objects.filter(is_active=True)
        for config in configs:
            service_class = service_map.get(config.service_type)
            if service_class:
                try:
                    service_instance = service_class(config)
                    all_tickets.extend(
                        service_instance.get_tickets(force_refresh=force_refresh),
                    )
                except Exception:
                    logger.exception("Fetch failed for service: %s", config.name)

        # 4. INITIAL SORT
        all_tickets.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        # =========================================================
        # 5. SECURITY GATEKEEPER (RBAC)
        # =========================================================

        found_groups = set()
        for t in all_tickets:
            if t.get("origin") and t.get("group"):
                found_groups.add((t["origin"], t["group"]))

        existing_groups = set(
            ExternalGroup.objects.filter(
                origin__in=[x[0] for x in found_groups],
                name__in=[x[1] for x in found_groups],
            ).values_list("origin", "name"),
        )

        new_groups = [
            ExternalGroup(origin=o, name=n)
            for o, n in found_groups
            if (o, n) not in existing_groups
        ]
        if new_groups:
            ExternalGroup.objects.bulk_create(new_groups, ignore_conflicts=True)

        allowed_tickets = []
        user_email = request.user.email

        if request.user.is_superuser:
            allowed_tickets = all_tickets
        else:
            perms = TicketPermission.objects.filter(
                django_group__in=request.user.groups.all(),
            ).values(
                "allowed_external_group__origin",
                "allowed_external_group__name",
                "access_level",
            )

            perm_map = {}
            for p in perms:
                key = "{}|{}".format(
                    p["allowed_external_group__origin"],
                    p["allowed_external_group__name"],
                )
                level = p["access_level"]
                if key not in perm_map or level == "FULL":
                    perm_map[key] = level

            for t in all_tickets:
                if t.get("owner_email") and t.get("owner_email") == user_email:
                    allowed_tickets.append(t)
                    continue

                key = f"{t.get('origin')}|{t.get('group')}"
                if key in perm_map:
                    level = perm_map[key]
                    if level == "FULL":
                        allowed_tickets.append(t)
                    elif level == "LIMITED":
                        owner = str(t.get("owner", ""))
                        if (
                            owner in ["Unassigned", "-", "", "None"]
                            or t.get("owner") is None
                        ):
                            allowed_tickets.append(t)
                    elif level == "OWN_ONLY":
                        # OWN_ONLY is handled by the global check above:
                        # if t.get("owner_email") == user_email: allowed_tickets.append(t)
                        # So here we don't need to do anything extra.
                        pass

        # =========================================================
        # 6. UI FILTERING & SORTING
        # =========================================================

        filtered_tickets = allowed_tickets

        # A. Focus Mode Logic
        selected_states = request.GET.getlist("state")
        selected_owners = request.GET.getlist("owner")
        query = request.GET.get("q", "").lower().strip()

        is_default_view = not (selected_states or selected_owners or query)

        if selected_states:
            filtered_tickets = [
                t for t in filtered_tickets if t.get("status") in selected_states
            ]
        elif is_default_view:
            filtered_tickets = [
                t for t in filtered_tickets if t.get("status") != "resolved"
            ]

        if selected_owners:
            want_unassigned = "Unassigned" in selected_owners
            specific_targets = set(selected_owners)
            if want_unassigned:
                specific_targets.discard("Unassigned")

            unassigned_markers = {None, "", "-", "None", "Unassigned"}

            new_filtered = []
            for t in filtered_tickets:
                is_match = False
                owner = t.get("owner")
                email = t.get("owner_email")

                # Check specific targets (Names or Emails)
                if specific_targets:
                    if (str(owner) in specific_targets or
                            str(email) in specific_targets):
                        is_match = True

                # Check unassigned (Only match if BOTH owner/email empty)
                if want_unassigned and not is_match:
                    owner_empty = (owner in unassigned_markers or
                                   str(owner) in unassigned_markers)
                    email_empty = (email in unassigned_markers or
                                   str(email) in unassigned_markers)
                    if owner_empty and email_empty:
                        is_match = True

                if is_match:
                    new_filtered.append(t)
            filtered_tickets = new_filtered
        elif is_default_view:
            filtered_tickets = [
                t
                for t in filtered_tickets
                if t.get("owner_email") == user_email
                or str(t.get("owner")) in ["Unassigned", "-", "None", ""]
                or t.get("owner") is None
            ]

        # B. Text Search
        if query:
            filtered_tickets = [
                t
                for t in filtered_tickets
                if query in str(t.get("title", "")).lower()
                or query in str(t.get("id", "")).lower()
                or query in str(t.get("customer", "")).lower()
                or query in str(t.get("owner", "")).lower()
            ]

        # C. Dropdowns
        def apply_dropdown(items, param, field):
            vals = request.GET.getlist(param)
            if vals:
                return [t for t in items if str(t.get(field)) in vals]
            return items

        filtered_tickets = apply_dropdown(filtered_tickets, "origin", "origin")
        filtered_tickets = apply_dropdown(filtered_tickets, "customer", "customer")
        filtered_tickets = apply_dropdown(filtered_tickets, "group", "group")
        # Note: We already handled "owner" specially above!
        filtered_tickets = apply_dropdown(filtered_tickets, "priority", "priority")

        # D. Date Range
        dr = request.GET.get("date_range")
        if dr and " to " in dr:
            try:
                start, end = dr.split(" to ")
                filtered_tickets = [
                    t
                    for t in filtered_tickets
                    if t.get("created_at") and start <= t.get("created_at") <= end
                ]
            except ValueError:
                pass

        # E. Sorting
        custom_sort = request.GET.get("sort")
        custom_dir = request.GET.get("direction", "desc")

        if custom_sort:
            reverse = custom_dir == "desc"

            def sort_key(t):
                val = t.get(custom_sort)
                if val is None:
                    # For due_date (or any other sort), None values go to the end
                    return "zzzzzzzzzz" if not reverse else ""
                return str(val).lower()

            filtered_tickets.sort(key=sort_key, reverse=reverse)
        else:

            def priority_sort(t):
                if t.get("owner_email") == user_email:
                    return 0
                owner = str(t.get("owner", ""))
                if owner in ["Unassigned", "-", "", "None"] or t.get("owner") is None:
                    return 1
                return 2

            filtered_tickets.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
            filtered_tickets.sort(key=priority_sort)

        # 7. GENERATE OPTIONS (FIXED: Extract Emails for Owners)
        def get_options(field):
            return sorted(
                {str(t.get(field, "")) for t in allowed_tickets if t.get(field)},
            )

        # Custom Logic for Owner Options: Prefer Email
        owner_options = set()
        has_unassigned = False
        unassigned_vals = ["Unassigned", "-", "None", ""]

        for t in allowed_tickets:
            owner = t.get("owner")
            owner_email = t.get("owner_email")

            # Check if this ticket is considered unassigned
            if (
                owner is None
                or owner_email is None
                or str(owner) in unassigned_vals
                or str(owner_email) in unassigned_vals
            ):
                has_unassigned = True

            val = owner_email if owner_email else owner
            if val and str(val) not in unassigned_vals:
                owner_options.add(str(val))

        owners = sorted(owner_options)
        if has_unassigned:
            owners.insert(0, "Unassigned")

        context["filter_options"] = {
            "customers": get_options("customer"),
            "groups": get_options("group"),
            "owners": owners,  # Sorted list of Emails/Names with Unassigned first if present
            "origins": get_options("origin"),
            "states": get_options("status"),
            "priorities": get_options("priority"),
        }

        # 8. STATS
        total = len(allowed_tickets)
        my_count = sum(
            1
            for t in allowed_tickets
            if t.get("owner_email") == user_email
            and t.get("status") in ["open", "pending", "new"]
        )

        context["stats"] = {
            "total": total,
            "my_tickets": my_count,
            "open": sum(1 for t in allowed_tickets if t.get("status") == "open"),
            "pending": sum(1 for t in allowed_tickets if t.get("status") == "pending"),
            "resolved": sum(
                1 for t in allowed_tickets if t.get("status") == "resolved"
            ),
        }

        # 9. PAGINATION
        paginator = Paginator(filtered_tickets, 50)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        context["tickets"] = page_obj
        context["custom_page_range"] = paginator.get_elided_page_range(
            page_obj.number,
            on_each_side=2,
            on_ends=1,
        )

        # 10. SAVED VIEWS
        saved_views = SavedView.objects.filter(user=self.request.user)
        context["saved_views"] = [
            {
                "id": v.id,
                "name": v.name,
                "params": v.query_params,
                "url": f"?{v.get_query_string()}",
            }
            for v in saved_views
        ]
        active_states = ["open", "pending", "new"]
        context["default_views"] = [
            {
                "name": "My Tickets",
                "params": {"owner": [user_email], "state": active_states},
                "url": f"?owner={user_email}&state=open&state=pending&state=new",
            },
            {
                "name": "Unassigned",
                "params": {"owner": ["Unassigned"], "state": active_states},
                "url": "?owner=Unassigned&state=open&state=pending&state=new",
            },
        ]

        return context


@login_required
@require_POST
def save_view(request):
    try:
        data = json.loads(request.body)
        name = data.get("name")
        query_params = data.get("query_params", {})

        if not name:
            return JsonResponse({"error": "Name is required"}, status=400)

        # Update or create the saved view
        view, created = SavedView.objects.update_or_create(
            user=request.user,
            name=name,
            defaults={"query_params": query_params},
        )

        return JsonResponse(
            {"status": "success", "id": view.id, "created": created},
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
def delete_saved_view(request, pk):
    view = get_object_or_404(SavedView, pk=pk, user=request.user)
    view.delete()
    return HttpResponseRedirect(reverse("users:dashboard"))
