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

from ticket_dashboard.services.gitlab import GitLabService

# Import Services and Models
from ticket_dashboard.services.zammad import ZammadService
from ticket_dashboard.users.models import ServiceConfiguration
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

        # 1. INITIALIZE LIST (Crucial Fix for NameError)
        all_tickets = []

        # 2. Setup Services & Configs
        force_refresh = request.GET.get("refresh") == "1"

        # Get or Create configs so they appear in Admin
        eramba_conf, _ = ServiceConfiguration.objects.get_or_create(name="Eramba")
        espo_conf, _ = ServiceConfiguration.objects.get_or_create(name="EspoCRM")
        op_conf, _ = ServiceConfiguration.objects.get_or_create(name="OpenProject")
        gitlab_conf, _ = ServiceConfiguration.objects.get_or_create(name="GitLab")
        zammad_conf, _ = ServiceConfiguration.objects.get_or_create(name="Zammad")

        # 3. Fetch Services
        if eramba_conf.is_active:
            try:
                eramba_service = ErambaService()
                eramba_tickets = eramba_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(eramba_tickets)
            except Exception:
                pass

        if espo_conf.is_active:
            try:
                espo_service = EspoService()
                espo_tickets = espo_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(espo_tickets)
            except Exception:
                pass

        if op_conf.is_active:
            try:
                op_service = OpenProjectService()
                op_tickets = op_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(op_tickets)
            except Exception:
                pass

        if gitlab_conf.is_active:
            try:
                gitlab_service = GitLabService()
                gl_tickets = gitlab_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(gl_tickets)
            except Exception:
                pass

        if zammad_conf.is_active:
            try:
                zammad_service = ZammadService()
                z_tickets = zammad_service.get_tickets(force_refresh=force_refresh)
                all_tickets.extend(z_tickets)
            except Exception:
                pass  # Logged in service, ignore here

        # 4. SORT COMBINED LIST (Newest Updated First)
        all_tickets.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        # 5. LOCAL FILTERING LOGIC
        filtered_tickets = all_tickets

        # A. Text Search (Title, ID, Customer, Owner)
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

        # 6. Generate Dynamic Filter Options (From TOTAL dataset)
        context["filter_options"] = {
            "customers": sorted(
                list(
                    set(
                        str(t.get("customer", ""))
                        for t in all_tickets
                        if t.get("customer")
                    )
                )
            ),
            "groups": sorted(
                list(
                    set(str(t.get("group", "")) for t in all_tickets if t.get("group"))
                )
            ),
            "owners": sorted(
                list(
                    set(
                        str(t.get("owner", ""))
                        for t in all_tickets
                        if t.get("owner")
                        and t.get("owner") != "Unassigned"
                        and t.get("owner") != "-"
                    )
                )
            ),
            "origins": sorted(
                list(
                    set(
                        str(t.get("origin", "")) for t in all_tickets if t.get("origin")
                    )
                )
            ),
            "states": sorted(
                list(
                    set(
                        str(t.get("status", "")) for t in all_tickets if t.get("status")
                    )
                )
            ),
            "priorities": sorted(
                list(
                    set(
                        str(t.get("priority", ""))
                        for t in all_tickets
                        if t.get("priority")
                    )
                )
            ),
        }

        # 7. Stats (Total Combined)
        context["stats"] = {
            "total": len(all_tickets),
            "open": sum(1 for t in all_tickets if t.get("status") == "open"),
            "pending": sum(1 for t in all_tickets if t.get("status") == "pending"),
            "resolved": sum(1 for t in all_tickets if t.get("status") == "resolved"),
        }

        # 8. Pagination
        paginator = Paginator(filtered_tickets, 30)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        custom_page_range = paginator.get_elided_page_range(
            page_obj.number, on_each_side=2, on_ends=1
        )

        context["tickets"] = page_obj
        context["custom_page_range"] = custom_page_range

        return context
