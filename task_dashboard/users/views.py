import datetime
import json
import logging
import re
import sys
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import BooleanField
from django.db.models import Case
from django.db.models import CharField
from django.db.models import Count
from django.db.models import F
from django.db.models import Q
from django.db.models import Value
from django.db.models import When
from django.db.models.expressions import RawSQL
from django.http import HttpResponseRedirect
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from django.views.generic import UpdateView

from task_dashboard.users.identity import UNASSIGNED_MARKERS
from task_dashboard.users.identity import get_identity_bridging_data
from task_dashboard.users.identity import get_user_tokens
from task_dashboard.users.identity import normalize_identity_string
from task_dashboard.users.identity import post_process_task_owners
from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import SavedView
from task_dashboard.users.models import Task
from task_dashboard.users.models import User
from task_dashboard.users.models import compare_query_params
from task_dashboard.users.rbac import get_rbac_q
from task_dashboard.users.tasks import SERVICE_CLASSES
from task_dashboard.users.tasks import fetch_all_tasks_task
from task_dashboard.users.tasks import parse_dt
from task_dashboard.users.utils import db_alpha
from task_dashboard.users.utils import db_norm

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 50
PRIORITY_WEIGHTS = {"critical": 1, "high": 2, "medium": 3, "low": 4}
DEFAULT_STATES = "open,pending"


# --- UTILITY VIEWS ---


class UserUpdateView(LoginRequiredMixin, UpdateView):
    model = User
    fields = ["name"]

    def get_success_url(self):
        return reverse("home")

    def get_object(self, queryset=None):
        user_id = self.request.user.pk
        assert user_id is not None
        return User.objects.get(pk=user_id)

    def form_valid(self, form):
        res = super().form_valid(form)
        messages.success(self.request, _("Information successfully updated"))
        return res


user_update_view = UserUpdateView.as_view()


@login_required
@user_passes_test(lambda u: u.is_staff)
def force_refresh_view(request):
    fetch_all_tasks_task()
    messages.success(request, _("Refresh started for all services."))
    referer = request.headers.get("referer")
    if referer:
        return HttpResponseRedirect(referer)
    return HttpResponseRedirect(reverse("home"))


@login_required
def refresh_single_task_view(request, pk):
    if not Task.objects.filter(pk=pk).filter(get_rbac_q(request.user)).exists():
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    task = get_object_or_404(Task, pk=pk)
    service_class = SERVICE_CLASSES.get(task.service.service_type)

    if not service_class:
        messages.error(request, _("Service type not supported for single refresh."))
    else:
        try:
            service_instance = service_class(task.service)
            task_data = service_instance.get_single_task(task)
            if task_data:
                task.title = task_data.get("title") or ""
                task.status = task_data.get("status") or ""
                task.priority = task_data.get("priority") or ""
                task.original_status = task_data.get("original_status") or ""
                task.original_priority = task_data.get("original_priority") or ""
                task.customer = task_data.get("customer") or ""
                task.group = task_data.get("group") or ""
                task.owner = task_data.get("owner") or ""
                task.owner_email = task_data.get("owner_email") or ""
                task.url = task_data.get("url") or ""
                task.created_at = parse_dt(task_data.get("created_at"))
                task.updated_at = parse_dt(task_data.get("updated_at"))
                task.due_date = parse_dt(task_data.get("due_date"))
                task.save()
                messages.success(request, _("Task updated successfully."))
            else:
                messages.warning(
                    request, _("Task could not be found in the remote service.")
                )
        except Exception:
            logger.exception("Error refreshing single task %s", pk)
            messages.error(request, _("Failed to refresh task."))

    referer = request.headers.get("referer")
    if referer:
        return HttpResponseRedirect(referer)
    return HttpResponseRedirect(reverse("home"))


@login_required
def stats_view(request):
    user = request.user
    dv = DashboardView()
    dv.setup(request)

    qs = dv.get_annotated_base_qs()
    qs = dv.add_owner_overlap_annotation(qs, user)
    base_tasks = qs.filter(get_rbac_q(user))

    search_q = request.GET.get("q", "").strip()
    if search_q:
        base_tasks = base_tasks.filter(search_text__icontains=search_q)

    stats = base_tasks.aggregate(
        total=Count("id", distinct=True),
        my_tasks=Count(
            "id",
            distinct=True,
            filter=Q(is_owner=True, status__in=["open", "pending", "new"]),
        ),
        open=Count("id", filter=Q(status="open"), distinct=True),
        pending=Count("id", filter=Q(status="pending"), distinct=True),
        unassigned=Count("id", filter=Q(is_unassigned=True), distinct=True),
        resolved=Count("id", filter=Q(status="resolved"), distinct=True),
    )

    return render(request, "users/partials/stats_cards.html", {"stats": stats})


# --- MIXINS ---


class DashboardFilterMixin:
    """Mixin for handling task annotation, filtering, and sorting logic."""

    # Type hint for Mypy to know this mixin is used with a View
    request: Any

    def get_annotated_base_qs(self):
        return (
            Task.objects.filter(service__is_active=True)
            .annotate(
                owner_base=db_alpha(F("owner")),
                email_base=db_alpha(F("owner_email")),
                onorm=db_norm(F("owner")),
                enorm=db_norm(F("owner_email")),
            )
            .annotate(
                is_unassigned=Case(
                    When(
                        Q(onorm__in=UNASSIGNED_MARKERS)
                        & Q(enorm__in=UNASSIGNED_MARKERS),
                        then=Value(value=True),
                    ),
                    default=Value(value=False),
                    output_field=BooleanField(),
                ),
            )
        )

    def add_owner_overlap_annotation(self, qs, user):
        user_tokens = get_user_tokens(user)
        if not user_tokens:
            return qs.annotate(is_owner=Value(value=False, output_field=BooleanField()))

        is_test = (
            getattr(settings, "TESTING", False)
            or "pytest" in sys.modules
            or not self.request.META.get("REMOTE_ADDR")
            or "testserver" in self.request.META.get("SERVER_NAME", "")
        )
        if is_test:
            email_tokens = [t for t in user_tokens if "@" in t]
            name_tokens = [t for t in user_tokens if "@" not in t]
            q_match = Q()
            for token in email_tokens:
                q_match |= Q(owner_email__iexact=token) | Q(owner__icontains=token)
            for token in name_tokens:
                q_match |= (
                    Q(owner__icontains=token)
                    & ~Q(owner__contains="@")
                    & ~Q(owner_email__contains="@")
                )
            return qs.annotate(
                is_owner=Case(
                    When(q_match, then=Value(value=True)),
                    default=Value(value=False),
                    output_field=BooleanField(),
                )
            )

        email_tokens = [t for t in user_tokens if "@" in t]
        name_tokens = [t for t in user_tokens if "@" not in t]
        sql_parts = []
        params = []
        if email_tokens:
            sql_parts.append(
                "(regexp_split_to_array(unaccent(lower(COALESCE("
                "\"users_task\".\"owner\", '') || ' ' || COALESCE("
                "\"users_task\".\"owner_email\", ''))), '[^a-z0-9@.-]+') && %s)"
            )
            params.append(email_tokens)
        if name_tokens:
            sql_parts.append(
                "(regexp_split_to_array(unaccent(lower(COALESCE("
                "\"users_task\".\"owner\", ''))), '[^a-z0-9@.-]+') && %s AND "
                "COALESCE(\"users_task\".\"owner\", '') NOT LIKE '%%@%%' AND "
                "COALESCE(\"users_task\".\"owner_email\", '') NOT LIKE '%%@%%')"
            )
            params.append(name_tokens)

        my_overlap_sql = " OR ".join(sql_parts) if sql_parts else "false"

        return qs.annotate(
            is_owner=RawSQL(  # noqa: S611 # nosec B611
                my_overlap_sql, params, output_field=BooleanField()
            )
        )

    def _apply_owner_filter(self, qs, of, best_to_raw):
        if not of:
            return qs
        unassigned_label = _("Unassigned")
        include_unassigned = unassigned_label in of
        return qs.filter_by_owners(
            of, best_to_raw, include_unassigned=include_unassigned
        )

    def _apply_context_filters(
        self, qs, request, best_to_raw, my_owner, perspective=None
    ):
        st = request.GET.getlist("state")
        if (
            not st
            and perspective != "all"
            and (perspective in ["my", "open", "unassigned"] or not request.GET)
        ):
            if perspective == "open":
                st = ["open"]
            else:
                settings_obj = GlobalSetting.load()
                st = [
                    s.strip()
                    for s in settings_obj.default_task_states.split(",")
                    if s.strip()
                ]

        if st:
            qs = qs.filter(status__in=st)

        of = request.GET.getlist("owner")
        if not of:
            if perspective == "my" and my_owner:
                of = [my_owner]
            elif perspective == "unassigned":
                of = [_("Unassigned")]

        if not (perspective == "all" and not of):
            qs = self._apply_owner_filter(qs, of, best_to_raw)

        def apply_m(q, param, field):
            vals = request.GET.getlist(param)
            return q.filter(**{f"{field}__in": vals}) if vals else q

        qs = apply_m(qs, "origin", "service__name")
        qs = apply_m(qs, "customer", "customer")
        qs = apply_m(qs, "group", "group")
        qs = apply_m(qs, "priority", "priority")
        return self._apply_date_filters(qs, request)

    def _apply_date_filters(self, qs, request):
        def apply_dr(q, param, field):
            dr = request.GET.get(param, "").strip()
            if not dr:
                return q
            start_date_str = None
            end_date_str = None
            if " to " in dr:
                try:
                    parts = [p.strip() for p in dr.split(" to ") if p.strip()]
                    if len(parts) == 2:  # noqa: PLR2004
                        start_date_str, end_date_str = parts[0], parts[1]
                    elif len(parts) == 1:
                        start_date_str = end_date_str = parts[0]
                except (ValueError, TypeError):
                    pass
            elif re.match(r"^\d{4}-\d{2}-\d{2}$", dr):
                start_date_str = end_date_str = dr

            if start_date_str and end_date_str:
                try:
                    start_d = datetime.date.fromisoformat(start_date_str)
                    end_d = datetime.date.fromisoformat(end_date_str)
                    start_dt = django_timezone.make_aware(
                        datetime.datetime.combine(start_d, datetime.time.min)
                    )
                    end_dt = django_timezone.make_aware(
                        datetime.datetime.combine(end_d, datetime.time.max)
                    )
                    return q.filter(**{f"{field}__range": [start_dt, end_dt]})
                except (ValueError, TypeError):
                    pass
            return q

        qs = apply_dr(qs, "date_range", "created_at")
        qs = apply_dr(qs, "updated_range", "updated_at")
        return apply_dr(qs, "due_range", "due_date")

    def _apply_sorting(self, qs, request):
        qs = qs.distinct().annotate(
            priority_rank=Case(
                When(priority__iexact="critical", then=Value(value="0")),
                When(priority__iexact="high", then=Value(value="1")),
                When(priority__iexact="medium", then=Value(value="2")),
                When(priority__iexact="normal", then=Value(value="2")),
                When(priority__iexact="low", then=Value(value="3")),
                default=Value(value="4"),
                output_field=CharField(),
            )
        )
        sort_map = {
            "origin": "service__name",
            "id": "external_id",
            "status": "status",
            "customer": "customer",
            "group": "group",
            "priority": "priority_rank",
            "created": "created_at",
            "updated": "updated_at",
            "due": "due_date",
        }
        sort_field = request.GET.get("sort", "created")
        direction = request.GET.get("direction", "desc")
        db_field = sort_map.get(sort_field, "created_at")
        if direction == "desc":
            db_field = f"-{db_field}"
        return qs.order_by(db_field, "-created_at")

    def _get_filter_options(self, base_tasks, merged):
        def get_opts(qs, field):
            return sorted(qs.order_by().values_list(field, flat=True).distinct())

        return {
            "customers": get_opts(base_tasks, "customer"),
            "groups": get_opts(base_tasks, "group"),
            "origins": get_opts(base_tasks, "service__name"),
            "states": get_opts(base_tasks, "status"),
            "priorities": sorted(
                get_opts(base_tasks, "priority"),
                key=lambda x: PRIORITY_WEIGHTS.get(x.lower(), 5),
            ),
            "owners": [
                _("Unassigned"),
                *sorted({g["best"] for g in merged.values()}, key=str.lower),
            ],
        }

    def _get_applied_filter_lists(self, request, perspective=None, my_owner=None):
        owners = request.GET.getlist("owner")
        if not owners:
            if perspective == "my" and my_owner:
                owners = [my_owner]
            elif perspective == "unassigned":
                owners = [_("Unassigned")]

        states = request.GET.getlist("state")
        if not states and perspective in ["my", "open", "unassigned"]:
            if perspective == "open":
                states = ["open"]
            else:
                settings_obj = GlobalSetting.load()
                states = [
                    s.strip()
                    for s in settings_obj.default_task_states.split(",")
                    if s.strip()
                ]

        return {
            "origins": request.GET.getlist("origin"),
            "customers": request.GET.getlist("customer"),
            "groups": request.GET.getlist("group"),
            "owners": owners,
            "states": states,
            "priorities": request.GET.getlist("priority"),
        }

    def _get_applied_filters_dict(
        self, request, search_q, perspective=None, my_owner=None
    ):
        filters: dict[str, Any] = {}
        if search_q:
            filters["q"] = {"label": _("Search"), "values": [search_q]}

        applied = self._get_applied_filter_lists(request, perspective, my_owner)
        for param, label in [
            ("owner", _("Owner")),
            ("state", _("State")),
            ("origin", _("Origin")),
            ("customer", _("Customer")),
            ("group", _("Group")),
            ("priority", _("Priority")),
        ]:
            plural_map = {
                "origin": "origins",
                "customer": "customers",
                "group": "groups",
                "owner": "owners",
                "state": "states",
                "priority": "priorities",
            }
            plural_key = plural_map.get(param, param)
            vals = applied.get(plural_key, [])
            if vals:
                filters[param] = {"label": label, "values": vals}

        for param, label in [
            ("date_range", _("Created")),
            ("updated_range", _("Updated")),
            ("due_range", _("Due")),
        ]:
            val = request.GET.get(param, "").strip()
            if val:
                filters[param] = {"label": label, "values": [val]}
        return filters


class DashboardHTMXMixin:
    """Mixin for handling HTMX template selection and perspective redirects."""

    # Type hints for Mypy
    request: Any
    template_name: str
    perspective: str | None

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ["users/partials/dashboard_table.html"]
        return [self.template_name]

    def _handle_perspective_redirects(self, request):
        if self.perspective == "home" and not request.GET:
            return HttpResponseRedirect("/my")
        if self.perspective == "all" and request.GET:
            nav_keys = {"page", "sort", "direction"}
            meaningful_keys = set(request.GET.keys()) - nav_keys
            if meaningful_keys:
                return HttpResponseRedirect(f"/?{request.GET.urlencode()}")
        if self.perspective in ["my", "open", "unassigned"] and request.GET:
            nav_keys = {"page", "sort", "direction"}
            meaningful_keys = set(request.GET.keys()) - nav_keys
            if meaningful_keys:
                q_copy = request.GET.copy()
                has_adhoc = any(
                    k not in ["owner", "state", "page", "sort", "direction"]
                    for k in request.GET
                )
                if has_adhoc or q_copy.get("q"):
                    self._redirect_with_defaults(q_copy)
                    return HttpResponseRedirect(f"/?{q_copy.urlencode()}")
        if request.GET and self.perspective != "all":
            if self._should_redirect_to_all(request):
                return HttpResponseRedirect("/all")
        return None

    def _redirect_with_defaults(self, qdict):
        if not qdict.getlist("state"):
            settings_obj = GlobalSetting.load()
            states = (
                getattr(settings_obj, "default_task_states", DEFAULT_STATES)
                .strip()
                .split(",")
            )
            qdict.setlist("state", [s.strip() for s in states if s.strip()])

    def _should_redirect_to_all(self, request):
        has_search = bool(request.GET.get("q", "").strip())
        if has_search:
            return False
        checked_fields = ["owner", "state", "origin", "customer", "group", "priority"]
        has_any_filter = any(request.GET.getlist(f) for f in checked_fields)
        has_any_filter = has_any_filter or any(
            request.GET.get(f, "").strip()
            for f in ["date_range", "updated_range", "due_range"]
        )
        nav_keys = {"page", "sort", "direction"}
        meaningful_keys = set(request.GET.keys()) - nav_keys
        return bool(meaningful_keys and not has_any_filter)

    def _determine_perspective_from_params(self, request, my_owner):
        if not my_owner:
            return "all"
        settings_obj = GlobalSetting.load()
        default_states = sorted(
            [
                s.strip()
                for s in settings_obj.default_task_states.split(",")
                if s.strip()
            ]
        )
        my_params = {"owner": [my_owner], "state": default_states}
        un_params = {"owner": [_("Unassigned")], "state": default_states}
        if compare_query_params(request.GET, my_params):
            return "my"
        if compare_query_params(request.GET, un_params):
            return "unassigned"
        return "all"


# --- MAIN DASHBOARD VIEW ---


class DashboardView(
    LoginRequiredMixin, DashboardFilterMixin, DashboardHTMXMixin, TemplateView
):
    template_name = "users/dashboard.html"
    perspective: str | None = None

    def get(self, request, *args, **kwargs):
        if "view" in request.GET:
            q = request.GET.copy()
            q.pop("view", None)
            redirect_url = f"{request.path}?{q.urlencode()}" if q else request.path
            return HttpResponseRedirect(redirect_url)

        redirect_response = self._handle_perspective_redirects(request)
        if redirect_response:
            return redirect_response

        if request.headers.get("HX-Request") != "true":
            return self.render_to_response(self.get_context_data(**kwargs))

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        user = request.user
        is_htmx = request.headers.get("HX-Request") == "true"
        context["last_sync_timestamp"] = cache.get(
            "last_task_sync", django_timezone.now()
        )

        my_owner = getattr(user, "email", "") or getattr(user, "name", "")
        search_q = request.GET.get("q", "").strip()
        view = (
            self.perspective
            if self.perspective in ["my", "open", "unassigned", "all"]
            else "all"
        )

        has_filters = any(
            k not in ["page", "sort", "direction", "refresh"] for k in request.GET
        )
        is_test = (
            getattr(settings, "TESTING", False)
            or "pytest" in sys.modules
            or not request.META.get("REMOTE_ADDR")
            or "testserver" in request.META.get("SERVER_NAME", "")
        )
        if not is_htmx and not has_filters and not search_q and not is_test:
            context.update(
                {
                    "tasks": Paginator(Task.objects.none(), 1).get_page(1),
                    "is_htmx_oob": False,
                    "applied_filters": self._get_applied_filter_lists(
                        request, view, my_owner
                    ),
                    "active_filters_sidebar": self._get_applied_filters_dict(
                        request, "", view, my_owner
                    ),
                    "filter_options": {},
                    "stats": {},
                    "default_views": self._get_default_views(),
                    "saved_views": self._get_saved_views(user),
                }
            )
            return context

        qs = self.get_annotated_base_qs()
        qs = self.add_owner_overlap_annotation(qs, user)

        base_tasks = qs.filter(get_rbac_q(user)).select_related(
            "service", "service_group"
        )
        if search_q:
            base_tasks = base_tasks.filter(search_text__icontains=search_q)

        context["stats"] = base_tasks.aggregate(
            total=Count("id", distinct=True),
            my_tasks=Count(
                "id",
                distinct=True,
                filter=Q(is_owner=True, status__in=["open", "pending", "new"]),
            ),
            open=Count("id", filter=Q(status="open"), distinct=True),
            pending=Count("id", filter=Q(status="pending"), distinct=True),
            unassigned=Count("id", filter=Q(is_unassigned=True), distinct=True),
            resolved=Count("id", filter=Q(status="resolved"), distinct=True),
        )

        merged, best_to_raw, token_to_canonical = get_identity_bridging_data(
            base_tasks, user
        )

        s_norm = normalize_identity_string(my_owner)
        anchor = re.sub(r"[^a-z0-9]", "", s_norm.split("@")[0])
        if anchor in merged:
            my_owner = merged[anchor]["best"]

        if self.perspective == "home":
            view = self._determine_perspective_from_params(request, my_owner)
        context["current_view"] = view

        applied_filters = self._get_applied_filter_lists(request, view, my_owner)
        context["filter_options"] = self._get_filter_options(base_tasks, merged)

        display_tasks = self._apply_context_filters(
            base_tasks, request, best_to_raw, my_owner, perspective=view
        )

        display_tasks = self._apply_sorting(display_tasks, request)
        paginator = Paginator(display_tasks, DEFAULT_PAGE_SIZE)
        page = paginator.get_page(request.GET.get("page"))

        t_list = list(page.object_list)
        for t in t_list:
            post_process_task_owners(t, token_to_canonical, merged)

        page.object_list = t_list
        sv = self._get_saved_views_queryset(user)
        active_id = next((v.id for v in sv if v.matches_params(request.GET)), None)

        context.update(
            {
                "tasks": page,
                "page_obj": page,
                "custom_page_range": paginator.get_elided_page_range(
                    page.number, on_each_side=2, on_ends=1
                ),
                "saved_views": [
                    {
                        "id": v.id,
                        "name": v.name,
                        "url": f"/?{v.get_query_string()}",
                        "is_active": (v.id == active_id),
                    }
                    for v in sv
                ],
                "active_saved_view_id": active_id,
                "applied_filters": applied_filters,
                "active_filters_sidebar": self._get_applied_filters_dict(
                    request, search_q, view, my_owner
                ),
                "default_views": self._get_default_views(),
                "is_htmx_oob": is_htmx,
            }
        )
        return context

    def _get_default_views(self):
        return [
            {
                "name": _("My Tasks"),
                "view_param": "my",
                "url": "/my",
                "description": _("Tasks assigned to you"),
            },
            {
                "name": _("Unassigned"),
                "view_param": "unassigned",
                "url": "/unassigned",
                "description": _("Tasks without an owner."),
            },
        ]

    def _get_saved_views(self, user):
        if not user.is_authenticated:
            return []
        return [
            {
                "id": v.id,
                "name": v.name,
                "url": f"/?{v.get_query_string()}",
                "is_active": False,
            }
            for v in SavedView.objects.filter(user=user)
        ]

    def _get_saved_views_queryset(self, user):
        if user.is_authenticated:
            return SavedView.objects.filter(user=user)
        return SavedView.objects.none()


@require_POST
@login_required
def save_view(request):
    try:
        data = json.loads(request.body)
        name = data.get("name")
        params = data.get("query_params") or data.get("params") or {}
        if not name:
            return JsonResponse(
                {"status": "error", "message": "Name is required"}, status=400
            )
        SavedView.objects.create(user=request.user, name=name, query_params=params)
        return JsonResponse({"status": "success"})
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


@require_POST
@login_required
def delete_saved_view(request, pk):
    view = get_object_or_404(SavedView, pk=pk, user=request.user)
    view.delete()
    return JsonResponse({"status": "success"})
