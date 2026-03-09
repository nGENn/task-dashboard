import datetime
import json
import logging
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpResponseRedirect
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import DetailView
from django.views.generic import RedirectView
from django.views.generic import TemplateView
from django.views.generic import UpdateView
from django_q.tasks import async_task

# Models
from task_dashboard.users.models import SavedView
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task
from task_dashboard.users.models import TaskPermission
from task_dashboard.users.models import User

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


@login_required
@require_POST
def force_refresh_view(request):
    """
    Triggers a fresh fetch from all services.
    Used by the "Force Refresh" button.
    """
    # Trigger background task
    async_task("task_dashboard.users.tasks.fetch_all_tasks_task")

    messages.info(
        request,
        _("Task refresh has been started in the background. Please wait a moment."),
    )

    # Clear health check cache to force fresh status as well
    active_configs = ServiceConfiguration.objects.filter(is_active=True)
    for config in active_configs:
        cache.delete(f"health_check_result_{config.pk}")

    # Redirect back to where the user came from, or dashboard
    referer = request.headers.get("referer")
    if referer:
        # Remove any existing refresh=1 from referer to be clean
        u = urlparse(referer)
        query = parse_qs(u.query)
        query.pop("refresh", None)
        u = u._replace(query=urlencode(query, doseq=True))
        return HttpResponseRedirect(urlunparse(u))

    return HttpResponseRedirect(reverse("users:dashboard"))


# --- DASHBOARD VIEW ---
class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "pages/home.html"

    def get(self, request, *args, **kwargs):
        if not request.GET:
            return HttpResponseRedirect(f"{request.path}?view=my")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):  # noqa: C901, PLR0912, PLR0915
        context = super().get_context_data(**kwargs)
        request = self.request

        # 1. RETRIEVE TASKS FROM DATABASE
        all_tasks = (
            Task.objects.filter(
                service__is_active=True,
            )
            .select_related("service")
            .order_by("-updated_at")
        )

        # =========================================================
        # 5. SECURITY GATEKEEPER (RBAC)
        # =========================================================

        allowed_tasks = []
        user_email = request.user.email

        # RBAC (Role-Based Access Control)
        # Check permissions using TaskPermission and ServiceConfiguration default
        perms = TaskPermission.objects.filter(
            django_group__in=request.user.groups.all(),
        ).values(
            "allowed_external_group__origin",
            "allowed_external_group__name",
            "access_level",
        )

        level_priority = {"NONE": 0, "OWN": 1, "LIMITED": 2, "FULL": 3}
        perm_map = {}
        for p in perms:
            key = "{}|{}".format(
                p["allowed_external_group__origin"],
                p["allowed_external_group__name"],
            )
            level = p["access_level"]
            if key not in perm_map or level_priority.get(level, 0) > level_priority.get(
                perm_map[key], 0
            ):
                perm_map[key] = level

        for t in all_tasks:
            t_origin = t.service.name
            t_group = t.group
            t_owner_email = t.owner_email
            t_owner = t.owner

            # RBAC: Use mapped permission or fallback to service default
            key = f"{t_origin}|{t_group}"
            level = perm_map.get(key, t.service.default_access_level)

            if level == "FULL":
                allowed_tasks.append(t)
            elif level == "LIMITED":
                # Own tasks OR Unassigned
                is_owner = t_owner_email and t_owner_email == user_email
                is_unassigned = (
                    str(t_owner) in ["Unassigned", "-", "", "None"] or t_owner is None
                )
                if is_owner or is_unassigned:
                    allowed_tasks.append(t)
            elif level == "OWN":
                # Only own tasks
                if t_owner_email and t_owner_email == user_email:
                    allowed_tasks.append(t)

        # =========================================================
        # 6. UI FILTERING & SORTING
        # =========================================================

        current_view = request.GET.get("view", "my")
        context["current_view"] = current_view

        # Apply Base View Context
        if current_view == "my":
            filtered_tasks = [t for t in allowed_tasks if t.owner_email == user_email]
        elif current_view == "unassigned":
            unassigned_markers = {None, "", "-", "None", "Unassigned"}
            filtered_tasks = [
                t
                for t in allowed_tasks
                if str(t.owner) in unassigned_markers
                and str(t.owner_email) in unassigned_markers
            ]
        else:
            filtered_tasks = allowed_tasks

        # A. Focus Mode Logic
        selected_states = request.GET.getlist("state")
        selected_owners = request.GET.getlist("owner")
        query = request.GET.get("q", "").lower().strip()

        # Apply default active states for 'my' and 'unassigned' views if no explicit
        # state filter is active
        if not selected_states and current_view in ["my", "unassigned"]:
            selected_states = ["open", "pending", "new"]

        if selected_states:
            filtered_tasks = [t for t in filtered_tasks if t.status in selected_states]

        if selected_owners:
            want_unassigned = "Unassigned" in selected_owners
            specific_targets = set(selected_owners)
            if want_unassigned:
                specific_targets.discard("Unassigned")

            unassigned_markers = {None, "", "-", "None", "Unassigned"}

            new_filtered = []
            for t in filtered_tasks:
                is_match = False
                owner = t.owner
                email = t.owner_email

                # Check specific targets (Names or Emails)
                if specific_targets:
                    if str(owner) in specific_targets or str(email) in specific_targets:
                        is_match = True

                # Check unassigned (Only match if BOTH owner/email empty)
                if want_unassigned and not is_match:
                    owner_empty = (
                        owner in unassigned_markers or str(owner) in unassigned_markers
                    )
                    email_empty = (
                        email in unassigned_markers or str(email) in unassigned_markers
                    )
                    if owner_empty and email_empty:
                        is_match = True

                if is_match:
                    new_filtered.append(t)
            filtered_tasks = new_filtered

        # B. Text Search
        if query:
            filtered_tasks = [
                t
                for t in filtered_tasks
                if query in str(t.title or "").lower()
                or query in str(t.external_id or "").lower()
                or query in str(t.customer or "").lower()
                or query in str(t.owner or "").lower()
            ]

        # C. Dropdowns
        def apply_dropdown(items, param, field):
            vals = request.GET.getlist(param)
            if vals:
                return [
                    t
                    for t in items
                    if str(getattr(t, field) if field != "origin" else t.service.name)
                    in vals
                ]
            return items

        filtered_tasks = apply_dropdown(filtered_tasks, "origin", "origin")
        filtered_tasks = apply_dropdown(filtered_tasks, "customer", "customer")
        filtered_tasks = apply_dropdown(filtered_tasks, "group", "group")
        # Note: We already handled "owner" specially above!
        filtered_tasks = apply_dropdown(filtered_tasks, "priority", "priority")

        # D. Date Range
        dr = request.GET.get("date_range")
        if dr and " to " in dr:
            try:
                start, end = dr.split(" to ")
                filtered_tasks = [
                    t
                    for t in filtered_tasks
                    if t.created_at and start <= str(t.created_at)[:10] <= end
                ]
            except ValueError:
                pass

        # E. Sorting
        custom_sort = request.GET.get("sort")
        custom_dir = request.GET.get("direction", "desc")

        if custom_sort:
            reverse = custom_dir == "desc"

            def sort_key(t):
                # Map template sort keys to model fields
                field_map = {
                    "origin": "service__name",
                    "id": "external_id",
                    "status": "status",
                    "priority": "priority",
                    "title": "title",
                    "customer": "customer",
                    "group": "group",
                    "owner": "owner_email",
                    "created_at": "created_at",
                    "updated_at": "updated_at",
                    "due_date": "due_date",
                }
                actual_field = field_map.get(custom_sort, custom_sort)

                # Get the value
                if "__" in actual_field:
                    parts = actual_field.split("__")
                    val = t
                    for p in parts:
                        val = getattr(val, p, None)
                else:
                    val = getattr(t, actual_field, None)

                if val is None:
                    # For due_date (or any other sort), None values go to the end
                    return "zzzzzzzzzz" if not reverse else ""
                return str(val).lower()

            filtered_tasks.sort(key=sort_key, reverse=reverse)
        else:
            # Fallback date for sorting (aware min date)
            min_date = datetime.datetime.min.replace(tzinfo=datetime.UTC)

            filtered_tasks.sort(
                key=lambda x: x.updated_at or min_date,
                reverse=True,
            )

        # 7. GENERATE OPTIONS (FIXED: Extract Emails for Owners)
        def get_options(field):
            if field == "origin":
                return sorted({t.service.name for t in allowed_tasks if t.service})
            if field == "status":
                return sorted({t.status for t in allowed_tasks if t.status})
            return sorted(
                {
                    str(getattr(t, field, ""))
                    for t in allowed_tasks
                    if getattr(t, field, "")
                },
            )

        # Custom Logic for Owner Options: Prefer Email
        owner_options = set()
        has_unassigned = False
        unassigned_vals = ["Unassigned", "-", "None", ""]

        for t in allowed_tasks:
            owner = t.owner
            owner_email = t.owner_email

            # Check if this task is considered unassigned
            if (
                not owner
                or not owner_email
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
            "owners": owners,
            "origins": get_options("origin"),
            "states": get_options("status"),
            "priorities": get_options("priority"),
        }

        # 8. STATS
        total = len(allowed_tasks)
        my_count = sum(
            1
            for t in allowed_tasks
            if t.owner_email == user_email and t.status in ["open", "pending", "new"]
        )

        context["stats"] = {
            "total": total,
            "my_tasks": my_count,
            "open": sum(1 for t in allowed_tasks if t.status == "open"),
            "pending": sum(1 for t in allowed_tasks if t.status == "pending"),
            "resolved": sum(1 for t in allowed_tasks if t.status == "resolved"),
        }

        # 9. PAGINATION
        paginator = Paginator(filtered_tasks, 50)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        context["tasks"] = page_obj
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
        context["default_views"] = [
            {
                "name": "My Tasks",
                "view_param": "my",
                "url": "?view=my",
            },
            {
                "name": "Unassigned",
                "view_param": "unassigned",
                "url": "?view=unassigned",
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
    except Exception:
        logger.exception("Error saving view")
        return JsonResponse({"error": "Internal server error"}, status=500)


@login_required
@require_POST
def delete_saved_view(request, pk):
    view = get_object_or_404(SavedView, pk=pk, user=request.user)
    view.delete()
    return HttpResponseRedirect(reverse("users:dashboard"))
