from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from django.views.generic import RedirectView
from django.views.generic import TemplateView
from django.views.generic import UpdateView

from ticket_dashboard.services.gitlab import GitLabService

# Import Services and Models
from ticket_dashboard.services.zammad import ZammadService
from ticket_dashboard.users.models import ExternalGroup
from ticket_dashboard.users.models import ServiceConfiguration
from ticket_dashboard.users.models import TicketPermission
from ticket_dashboard.users.models import User


# --- USER VIEWS (Keep these) ---
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
        assert self.request.user.is_authenticated
        return self.request.user.get_absolute_url()

    def get_object(self, queryset: QuerySet | None = None) -> User:
        assert self.request.user.is_authenticated
        return self.request.user


user_update_view = UserUpdateView.as_view()


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self) -> str:
        return reverse("users:detail", kwargs={"pk": self.request.user.pk})


user_redirect_view = UserRedirectView.as_view()


# --- DASHBOARD VIEW (The Fix) ---
class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request

        # 1. INITIALIZE LIST
        all_tickets = []

        # 2. Setup Services & Configs
        force_refresh = request.GET.get("refresh") == "1"

        # Get or Create configs so they appear in Admin
        zammad_conf, _ = ServiceConfiguration.objects.get_or_create(name="Zammad")
        gitlab_conf, _ = ServiceConfiguration.objects.get_or_create(name="GitLab")
        espo_conf, _ = ServiceConfiguration.objects.get_or_create(name="EspoCRM")
        op_conf, _ = ServiceConfiguration.objects.get_or_create(name="OpenProject")
        eramba_conf, _ = ServiceConfiguration.objects.get_or_create(name="Eramba")

        # 3. FETCH ACTIVE SERVICES
        # Zammad
        if zammad_conf.is_active:
            try:
                zammad_service = ZammadService()
                z_tickets = zammad_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(z_tickets)
            except Exception:
                pass

        # GitLab
        if gitlab_conf.is_active:
            try:
                gitlab_service = GitLabService()
                gl_tickets = gitlab_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(gl_tickets)
            except Exception:
                pass

        # EspoCRM
        if espo_conf.is_active:
            try:
                espo_service = EspoService()
                espo_tickets = espo_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(espo_tickets)
            except Exception:
                pass

        # OpenProject
        if op_conf.is_active:
            try:
                op_service = OpenProjectService()
                op_tickets = op_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(op_tickets)
            except Exception:
                pass

        # Eramba
        if eramba_conf.is_active:
            try:
                eramba_service = ErambaService()
                eramba_tickets = eramba_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(eramba_tickets)
            except Exception:
                pass

        # 4. SORT COMBINED LIST (Initial Sort)
        # We do this before filtering to ensure consistent order
        sort_by = request.GET.get("sort", "updated_at")
        sort_dir = request.GET.get("direction", "desc")

        valid_keys = [
            "origin",
            "id",
            "title",
            "customer",
            "group",
            "owner",
            "status",
            "priority",
            "created_at",
            "updated_at",
        ]
        if sort_by not in valid_keys:
            sort_by = "updated_at"

        reverse_sort = sort_dir == "desc"

        def sort_key(t):
            val = t.get(sort_by)
            if val is None:
                return ""
            return str(val).lower()

        all_tickets.sort(key=sort_key, reverse=reverse_sort)

        # =========================================================
        # 5. SECURITY GATEKEEPER & AUTO-DISCOVERY
        # =========================================================

        # A. Auto-Discovery: Save unknown groups to DB
        found_groups = set()
        for t in all_tickets:
            # Only add if both origin and group exist
            if t.get("origin") and t.get("group"):
                found_groups.add((t["origin"], t["group"]))

        # Bulk check/create to minimize DB hits
        existing_groups = set(
            ExternalGroup.objects.filter(
                origin__in=[x[0] for x in found_groups],
                name__in=[x[1] for x in found_groups],
            ).values_list("origin", "name")
        )

        new_groups_to_create = []
        for origin, group_name in found_groups:
            if (origin, group_name) not in existing_groups:
                new_groups_to_create.append(
                    ExternalGroup(origin=origin, name=group_name)
                )
                existing_groups.add((origin, group_name))  # Prevent duplicates in loop

        if new_groups_to_create:
            ExternalGroup.objects.bulk_create(
                new_groups_to_create, ignore_conflicts=True
            )

        # B. Filter by Permission (Upgraded Gatekeeper)
        allowed_tickets = []

        if request.user.is_superuser:
            allowed_tickets = all_tickets
        else:
            user_email = request.user.email

            # 1. Fetch Permissions with Levels
            # We fetch the specific Access Level for each allowed group
            permissions = TicketPermission.objects.filter(
                django_group__in=request.user.groups.all()
            ).values(
                "allowed_external_group__origin",
                "allowed_external_group__name",
                "access_level",
            )

            # 2. Build a Permission Map: "Origin|Group" -> "Access Level"
            # Logic: If a user is in multiple groups that grant access to the SAME external group,
            # we prioritize "FULL" access over "LIMITED".
            perm_map = {}
            for p in permissions:
                key = f"{p['allowed_external_group__origin']}|{p['allowed_external_group__name']}"
                level = p["access_level"]

                # If we haven't seen this group yet, OR if the new perm is 'FULL', save it.
                # (This ensures FULL overrides LIMITED if conflicts exist)
                if key not in perm_map or level == "FULL":
                    perm_map[key] = level

            # 3. Filter Tickets
            for t in all_tickets:
                # Rule 1: Always see your own tickets (Absolute Safety Net)
                # Matches Email OR Name (fallback)
                is_owner = t.get("owner_email") and t.get("owner_email") == user_email
                if is_owner:
                    allowed_tickets.append(t)
                    continue

                # Rule 2: Group Permissions
                ticket_key = f"{t.get('origin')}|{t.get('group')}"

                if ticket_key in perm_map:
                    access = perm_map[ticket_key]

                    if access == "FULL":
                        # User has full visibility for this group
                        allowed_tickets.append(t)

                    elif access == "LIMITED":
                        # User can only see if:
                        # a. They own it (Already handled by Rule 1)
                        # b. It is Unassigned
                        owner_name = str(t.get("owner", ""))
                        is_unassigned = (
                            owner_name in ["Unassigned", "-", "", "None"]
                            or t.get("owner") is None
                        )

                        if is_unassigned:
                            allowed_tickets.append(t)

        # 6. LOCAL UI FILTERING (Applied to allowed_tickets)
        filtered_tickets = allowed_tickets

        # A. Text Search
        query = request.GET.get("q", "").lower().strip()
        if query:
            filtered_tickets = [
                t
                for t in filtered_tickets
                if query in str(t.get("title", "")).lower()
                or query in str(t.get("id", "")).lower()
                or query in str(t.get("customer", "")).lower()
                or query in str(t.get("owner", "")).lower()
            ]

        # B. Dropdown Filters
        def apply_filter(tickets, param_name, field_name):
            values = request.GET.getlist(param_name)
            if values:
                return [t for t in tickets if str(t.get(field_name)) in values]
            return tickets

        filtered_tickets = apply_filter(filtered_tickets, "origin", "origin")
        filtered_tickets = apply_filter(filtered_tickets, "customer", "customer")
        filtered_tickets = apply_filter(filtered_tickets, "group", "group")
        filtered_tickets = apply_filter(filtered_tickets, "owner", "owner")
        filtered_tickets = apply_filter(filtered_tickets, "state", "status")
        filtered_tickets = apply_filter(filtered_tickets, "priority", "priority")

        # C. Date Range Filter
        date_range = request.GET.get("date_range")
        if date_range and " to " in date_range:
            try:
                start_date, end_date = date_range.split(" to ")
                filtered_tickets = [
                    t
                    for t in filtered_tickets
                    if t.get("created_at")
                    and start_date <= t.get("created_at") <= end_date
                ]
            except ValueError:
                pass

        # 7. Generate Filter Options (From ALLOWED dataset)
        # We use 'allowed_tickets' here so users only see options they have access to
        context["filter_options"] = {
            "customers": sorted(
                list(
                    set(
                        str(t.get("customer", ""))
                        for t in allowed_tickets
                        if t.get("customer")
                    )
                )
            ),
            "groups": sorted(
                list(
                    set(
                        str(t.get("group", ""))
                        for t in allowed_tickets
                        if t.get("group")
                    )
                )
            ),
            "owners": sorted(
                list(
                    set(
                        str(t.get("owner", ""))
                        for t in allowed_tickets
                        if t.get("owner")
                        and t.get("owner") != "Unassigned"
                        and t.get("owner") != "-"
                    )
                )
            ),
            "origins": sorted(
                list(
                    set(
                        str(t.get("origin", ""))
                        for t in allowed_tickets
                        if t.get("origin")
                    )
                )
            ),
            "states": sorted(
                list(
                    set(
                        str(t.get("status", ""))
                        for t in allowed_tickets
                        if t.get("status")
                    )
                )
            ),
            "priorities": sorted(
                list(
                    set(
                        str(t.get("priority", ""))
                        for t in allowed_tickets
                        if t.get("priority")
                    )
                )
            ),
        }

        # 8. Stats (Calculated on ALLOWED dataset)
        context["stats"] = {
            "total": len(allowed_tickets),
            "open": sum(1 for t in allowed_tickets if t.get("status") == "open"),
            "pending": sum(1 for t in allowed_tickets if t.get("status") == "pending"),
            "resolved": sum(
                1 for t in allowed_tickets if t.get("status") == "resolved"
            ),
        }

        # 9. Pagination
        paginator = Paginator(filtered_tickets, 30)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        custom_page_range = paginator.get_elided_page_range(
            page_obj.number, on_each_side=2, on_ends=1
        )

        context["tickets"] = page_obj
        context["custom_page_range"] = custom_page_range

        return context
