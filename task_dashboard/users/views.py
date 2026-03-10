import datetime
import json
import logging
import re
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
        all_tasks = list(
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
        user_name = getattr(request.user, "name", "")

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

        unassigned_markers = {None, "", "-", "None", "Unassigned"}

        # 1. Build Canonical Owner Mapping
        # This logic handles various owner identifier formats (full names, emails, GitLab usernames)
        # and groups them by "canonical" last names to unify identities across services.
        last_names = set()

        def extract_base(val, is_email=False):
            """
            Normalizes a string to its base component (email prefix or last name).
            Example: 'john.doe@example.com' -> 'johndoe', 'John Doe' -> 'doe'
            """
            if not val or str(val) in unassigned_markers:
                return ""
            v = str(val).lower().strip()
            if is_email and "@" in v:
                v = v.split("@")[0]
            elif " " in v:
                v = v.split()[-1]
            # Remove all non-alphanumeric characters
            return re.sub(r'[^a-z0-9]', '', v)

        # Collect potential last names from current user and all tasks
        if user_email: last_names.add(extract_base(user_email, True))
        if user_name: last_names.add(extract_base(user_name))

        for t in all_tasks:
            ln_email = extract_base(t.owner_email, True)
            if ln_email: last_names.add(ln_email)
            ln_owner = extract_base(t.owner)
            if ln_owner: last_names.add(ln_owner)

        # We filter for strings length >= 4 to avoid matching too many short usernames (like 'm1')
        # sorting by length ASC means shorter matches take precedence if they fit.
        sorted_last_names = sorted([ln for ln in last_names if len(ln) >= 4], key=len)

        def get_canonical(val, is_email=False):
            """
            Finds the best canonical 'last name' for a given value.
            Supports GitLab usernames like 'jdoe' matching 'doe'.
            """
            base = extract_base(val, is_email)
            if not base:
                return ""
            for ln in sorted_last_names:
                # Match if base ends with known last name (e.g. jdoe ends with doe)
                # and the prefix is short (likely initials, max 3 chars)
                if base.endswith(ln) and len(base) <= len(ln) + 3:
                    return ln
            return base

        # Precompute canonical identities for the current user
        user_canons = set()
        c_uemail = get_canonical(user_email, True)
        if c_uemail: user_canons.add(c_uemail)
        c_uname = get_canonical(user_name)
        if c_uname: user_canons.add(c_uname)

        # Map every task to its set of canonical owner identities
        task_canonicals = {}
        for t in all_tasks:
            c_email = get_canonical(t.owner_email, True)
            c_owner = get_canonical(t.owner)
            canons = set()
            if c_email: canons.add(c_email)
            if c_owner: canons.add(c_owner)
            task_canonicals[t.id] = canons

        for t in all_tasks:
            t_origin = t.service.name
            t_group = t.group

            # RBAC: Use mapped permission or fallback to service default
            key = f"{t_origin}|{t_group}"
            level = perm_map.get(key, t.service.default_access_level)

            if level == "FULL":
                allowed_tasks.append(t)
            elif level == "LIMITED":
                # Own tasks (Canonical match) OR Unassigned
                t_canons = task_canonicals[t.id]
                is_owner = bool(user_canons.intersection(t_canons)) if user_canons else False
                is_unassigned = not t_canons
                if is_owner or is_unassigned:
                    allowed_tasks.append(t)
            elif level == "OWN":
                # Only own tasks (Canonical match)
                t_canons = task_canonicals[t.id]
                is_owner = bool(user_canons.intersection(t_canons)) if user_canons else False
                if is_owner:
                    allowed_tasks.append(t)

        # =========================================================
        # 6. UI FILTERING & SORTING
        # =========================================================

        current_view = request.GET.get("view", "my")

        # UX IMPROVEMENT: If filtering or searching, default to 'all' view unless view explicitly set
        # This makes it easier to find tasks across the whole system when a specific filter is applied.
        filter_params = ["owner", "q", "state", "origin", "customer", "group", "priority", "date_range"]
        is_filtering = any(request.GET.get(p) for p in filter_params)

        if is_filtering and "view" not in request.GET:
            current_view = "all"

        context["current_view"] = current_view

        # Apply Base View Context
        if current_view == "my":
            filtered_tasks = [
                t
                for t in allowed_tasks
                if user_canons and task_canonicals[t.id].intersection(user_canons)
            ]
        elif current_view == "unassigned":
            filtered_tasks = [
                t
                for t in allowed_tasks
                if not task_canonicals[t.id]
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
            
            canonical_targets = {
                get_canonical(o, "@" in o)
                for o in selected_owners if o != "Unassigned"
            }

            new_filtered = []
            for t in filtered_tasks:
                t_canons = task_canonicals[t.id]
                is_match = False
                
                if t_canons and t_canons.intersection(canonical_targets):
                    is_match = True
                elif not t_canons and want_unassigned:
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

        # Custom Logic for Owner Options: Group by Canonical Identity
        canonical_to_best_name = {}
        # Map canonical identity to the unified email for table display
        canonical_to_email = {}
        has_unassigned = False

        for t in allowed_tasks:
            t_canons = task_canonicals[t.id]
            if not t_canons:
                has_unassigned = True
                continue

            for c in t_canons:
                # 1. Update best name for the dropdown (PRIORITIZE EMAIL)
                current_best = canonical_to_best_name.get(c, "")
                candidates = []
                
                # If this canonical identity matches the current user, prefer user model's email/name
                if user_canons and c in user_canons:
                    if user_email: candidates.append(user_email)
                    if user_name: candidates.append(user_name)

                if t.owner_email and str(t.owner_email) not in unassigned_markers:
                    candidates.append(str(t.owner_email))
                if t.owner and str(t.owner) not in unassigned_markers:
                    candidates.append(str(t.owner))

                for cand in candidates:
                    # Preference: Email (@) > Full Name (space) > Username
                    if "@" in cand and "@" not in current_best:
                        current_best = cand
                    elif " " in cand and "@" not in current_best and " " not in current_best:
                        current_best = cand
                    elif not current_best:
                        current_best = cand

                canonical_to_best_name[c] = current_best
                
                # 2. Track canonical email for table display
                if "@" in current_best:
                    canonical_to_email[c] = current_best

        # Unify owner display in the table: if a canonical email exists, use it.
        for t in allowed_tasks:
            t_canons = task_canonicals[t.id]
            for c in t_canons:
                if c in canonical_to_email:
                    t.owner_email = canonical_to_email[c]
                    # Also update owner name to empty if we have an email to avoid double display if template handles both
                    break

        owners = sorted(list(set(canonical_to_best_name.values())))
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
            if user_canons and task_canonicals[t.id].intersection(user_canons)
            and t.status in ["open", "pending", "new"]
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
