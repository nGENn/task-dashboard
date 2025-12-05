import logging

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
from ticket_dashboard.users.models import ExternalGroup
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request

        # 1. INITIALIZE LIST
        all_tickets = []
        force_refresh = request.GET.get("refresh") == "1"

        # 2. CONFIGS
        zammad_conf, _ = ServiceConfiguration.objects.get_or_create(name="Zammad")
        gitlab_conf, _ = ServiceConfiguration.objects.get_or_create(name="GitLab")
        espo_conf, _ = ServiceConfiguration.objects.get_or_create(name="EspoCRM")
        op_conf, _ = ServiceConfiguration.objects.get_or_create(name="OpenProject")
        eramba_conf, _ = ServiceConfiguration.objects.get_or_create(name="Eramba")

        # 3. FETCH SERVICES
        # We fetch everything (including closed) so we can filter locally
        if zammad_conf.is_active:
            try:
                all_tickets.extend(
                    ZammadService().get_tickets(force_refresh=force_refresh)
                )
            except Exception as e:
                logger.error(f"Zammad fetch failed: {e}")

        if gitlab_conf.is_active:
            try:
                all_tickets.extend(
                    GitLabService().get_tickets(force_refresh=force_refresh)
                )
            except Exception as e:
                logger.error(f"GitLab fetch failed: {e}")

        if espo_conf.is_active:
            try:
                all_tickets.extend(
                    EspoService().get_tickets(force_refresh=force_refresh)
                )
            except Exception as e:
                logger.error(f"EspoCRM fetch failed: {e}")

        if op_conf.is_active:
            try:
                all_tickets.extend(
                    OpenProjectService().get_tickets(force_refresh=force_refresh)
                )
            except Exception as e:
                logger.error(f"OpenProject fetch failed: {e}")

        if eramba_conf.is_active:
            try:
                all_tickets.extend(
                    ErambaService().get_tickets(force_refresh=force_refresh)
                )
            except Exception as e:
                logger.error(f"Eramba fetch failed: {e}")

        # 4. INITIAL SORT (Newest first)
        # Ensures stable order before any other operations
        all_tickets.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        # =========================================================
        # 5. SECURITY GATEKEEPER (RBAC)
        # =========================================================

        # A. Auto-Discovery
        found_groups = set()
        for t in all_tickets:
            if t.get("origin") and t.get("group"):
                found_groups.add((t["origin"], t["group"]))

        existing_groups = set(
            ExternalGroup.objects.filter(
                origin__in=[x[0] for x in found_groups],
                name__in=[x[1] for x in found_groups],
            ).values_list("origin", "name")
        )

        new_groups = [
            ExternalGroup(origin=o, name=n)
            for o, n in found_groups
            if (o, n) not in existing_groups
        ]
        if new_groups:
            ExternalGroup.objects.bulk_create(new_groups, ignore_conflicts=True)

        # B. Filter by Permission
        allowed_tickets = []
        user_email = request.user.email

        if request.user.is_superuser:
            allowed_tickets = all_tickets
        else:
            perms = TicketPermission.objects.filter(
                django_group__in=request.user.groups.all()
            ).values(
                "allowed_external_group__origin",
                "allowed_external_group__name",
                "access_level",
            )

            perm_map = {}
            for p in perms:
                key = f"{p['allowed_external_group__origin']}|{p['allowed_external_group__name']}"
                level = p["access_level"]
                if key not in perm_map or level == "FULL":
                    perm_map[key] = level

            for t in all_tickets:
                # Rule 1: Own tickets (Safety Net)
                if t.get("owner_email") and t.get("owner_email") == user_email:
                    allowed_tickets.append(t)
                    continue

                # Rule 2: Group Permissions
                key = f"{t.get('origin')}|{t.get('group')}"
                if key in perm_map:
                    level = perm_map[key]
                    if level == "FULL":
                        allowed_tickets.append(t)
                    elif level == "LIMITED":
                        # Limited: See only Unassigned
                        owner = str(t.get("owner", ""))
                        if (
                            owner in ["Unassigned", "-", "", "None"]
                            or t.get("owner") is None
                        ):
                            allowed_tickets.append(t)

        # =========================================================
        # 6. UI FILTERING & SORTING
        # =========================================================

        filtered_tickets = allowed_tickets

        # A. Default "Focus Mode" Logic
        # If no specific filters are active, hide noise (Closed & Other people's tickets)

        selected_states = request.GET.getlist("state")
        selected_owners = request.GET.getlist("owner")
        query = request.GET.get("q", "").lower().strip()

        is_default_view = not (selected_states or selected_owners or query)

        # Logic: Filter States
        if selected_states:
            filtered_tickets = [
                t for t in filtered_tickets if t.get("status") in selected_states
            ]
        elif is_default_view:
            # Default: Hide resolved/closed if no state selected
            filtered_tickets = [
                t for t in filtered_tickets if t.get("status") != "resolved"
            ]

        # Logic: Filter Owners
        if selected_owners:
            filtered_tickets = [
                t for t in filtered_tickets if str(t.get("owner")) in selected_owners
            ]
        elif is_default_view:
            # Default: Show ONLY My Tickets + Unassigned
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

        # C. Other Dropdowns
        def apply_dropdown(items, param, field):
            vals = request.GET.getlist(param)
            if vals:
                return [t for t in items if str(t.get(field)) in vals]
            return items

        filtered_tickets = apply_dropdown(filtered_tickets, "origin", "origin")
        filtered_tickets = apply_dropdown(filtered_tickets, "customer", "customer")
        filtered_tickets = apply_dropdown(filtered_tickets, "group", "group")
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
        # If user clicked a header, use that. Otherwise use "Focus Sort"
        custom_sort = request.GET.get("sort")
        custom_dir = request.GET.get("direction", "desc")

        if custom_sort:
            reverse = custom_dir == "desc"

            def sort_key(t):
                val = t.get(custom_sort)
                return str(val).lower() if val is not None else ""

            filtered_tickets.sort(key=sort_key, reverse=reverse)
        else:
            # Default Sort: 1. Own Tickets, 2. Unassigned, 3. Others (Sorted by Updated)
            def priority_sort(t):
                if t.get("owner_email") == user_email:
                    return 0
                owner = str(t.get("owner", ""))
                if owner in ["Unassigned", "-", "", "None"] or t.get("owner") is None:
                    return 1
                return 2

            # Sort by updated first (secondary sort)
            filtered_tickets.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
            # Then stable sort by priority
            filtered_tickets.sort(key=priority_sort)

        # 7. OPTIONS & STATS (From ALLOWED dataset, so users see total potential workload)
        def get_options(field):
            return sorted(
                list(
                    set(str(t.get(field, "")) for t in allowed_tickets if t.get(field))
                )
            )

        raw_owners = get_options("owner")
        clean_owners = [o for o in raw_owners if o not in ["Unassigned", "-", "None"]]

        context["filter_options"] = {
            "customers": get_options("customer"),
            "groups": get_options("group"),
            "owners": clean_owners,
            "origins": get_options("origin"),
            "states": get_options("status"),
            "priorities": get_options("priority"),
        }

        total = len(allowed_tickets)
        my_count = sum(1 for t in allowed_tickets if t.get("owner_email") == user_email)

        context["stats"] = {
            "total": total,
            "my_tickets": my_count,  # <--- NEW STAT
            "open": sum(1 for t in allowed_tickets if t.get("status") == "open"),
            "pending": sum(1 for t in allowed_tickets if t.get("status") == "pending"),
            "resolved": sum(
                1 for t in allowed_tickets if t.get("status") == "resolved"
            ),
        }

        # 8. PAGINATION
        paginator = Paginator(filtered_tickets, 30)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        context["tickets"] = page_obj
        context["custom_page_range"] = paginator.get_elided_page_range(
            page_obj.number, on_each_side=2, on_ends=1
        )

        return context
