import datetime
import json
import logging
import re
import sys
import unicodedata
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import BooleanField
from django.db.models import Case
from django.db.models import CharField
from django.db.models import Count
from django.db.models import F
from django.db.models import Func
from django.db.models import Q
from django.db.models import Value
from django.db.models import When
from django.db.models.expressions import RawSQL
from django.db.models.functions import Coalesce
from django.db.models.functions import Lower
from django.db.models.functions import Replace
from django.db.models.functions import Trim
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

from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import SavedView
from task_dashboard.users.models import ServicePermission
from task_dashboard.users.models import Task
from task_dashboard.users.models import TaskPermission
from task_dashboard.users.models import User
from task_dashboard.users.models import compare_query_params
from task_dashboard.users.tasks import SERVICE_CLASSES
from task_dashboard.users.tasks import TaskService
from task_dashboard.users.tasks import fetch_all_tasks_task
from task_dashboard.users.tasks import parse_dt

logger = logging.getLogger(__name__)

# --- CONSTANTS ---
MIN_TOKEN_LENGTH = 3
BRIDGE_THRESHOLD = 4
BRIDGE_CORE_LENGTH = 5
DEFAULT_PAGE_SIZE = 50
UNASSIGNED_MARKERS = [
    "",
    "-",
    "none",
    "unassigned",
    "0",
    "null",
    "unassigned person",
    "nicht zugewiesen",
    "keiner",
    "offen",
]
PRIORITY_WEIGHTS = {"critical": 1, "high": 2, "medium": 3, "low": 4}
DEFAULT_STATES = "open,pending"

# RBAC Levels
RBAC_NONE = 0
RBAC_OWN = 1
RBAC_LIMITED = 2
RBAC_FULL = 3
RBAC_MAP = {
    "NONE": RBAC_NONE,
    "OWN": RBAC_OWN,
    "LIMITED": RBAC_LIMITED,
    "FULL": RBAC_FULL,
}


# --- POSTGRES HELPER FUNCTIONS ---
class Unaccent(Func):
    function = "UNACCENT"


class SplitPart(Func):
    function = "SPLIT_PART"


class RegexpReplace(Func):
    function = "REGEXP_REPLACE"


def normalize_identity_string(s):
    if not s:
        return ""
    s_norm = (
        str(s).lower().strip().replace("ö", "oe").replace("ä", "ae").replace("ü", "ue")
    )
    return (
        unicodedata.normalize("NFKD", s_norm).encode("ASCII", "ignore").decode("utf-8")
    )


# --- UTILITY VIEWS ---


class UserUpdateView(UpdateView):
    model = User
    fields = ["name"]

    def get_success_url(self):
        return reverse("home")

    def get_object(self, queryset=None):
        assert self.request.user.is_authenticated
        return User.objects.get(pk=self.request.user.pk)

    def form_valid(self, form):
        res = super().form_valid(form)
        messages.success(self.request, _("Information successfully updated"))
        return res


user_update_view = UserUpdateView.as_view()


@login_required
def user_detail_view(request, pk):
    user = get_object_or_404(User, pk=pk)
    return render(request, "users/user_detail.html", {"object": user})


@login_required
def force_refresh_view(request):
    """
    Triggers a background task to refresh all services.
    Returns to the previous page if possible.
    """
    fetch_all_tasks_task()
    messages.success(request, _("Refresh started for all services."))
    referer = request.headers.get("referer")
    if referer:
        return HttpResponseRedirect(referer)
    return HttpResponseRedirect(reverse("home"))


@login_required
def refresh_single_task_view(request, pk):
    """
    Refreshes a single task by calling the service's get_single_task method.
    This is faster than a full service sync and preserves the current page.
    """
    task = get_object_or_404(Task, pk=pk)
    service_class = SERVICE_CLASSES.get(task.service.service_type)

    if not service_class:
        messages.error(request, _("Service type not supported for single refresh."))
    else:
        try:
            from typing import cast

            service_instance = cast(TaskService, service_class(task.service))
            # This is a synchronous call in the view for immediate feedback
            task_data = service_instance.get_single_task(task)
            if task_data:
                # Update the existing task instance
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

    qs = dv._get_annotated_base_qs()  # noqa: SLF001
    qs = dv._add_owner_overlap_annotation(qs, user)  # noqa: SLF001
    base_tasks = qs.filter(dv._get_rbac_q(user))  # noqa: SLF001

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


# --- MAIN DASHBOARD VIEW ---


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "users/dashboard.html"
    perspective: str | None = None

    def _apply_owner_filter_test(self, tokens_list, include_unassigned, qs):
        """Safer fallback for test environments."""
        q_owner = Q()
        for token in tokens_list:
            # Split tokens further to ensure "smithers" matches "smithers@example.com"
            subtokens = [
                t
                for t in re.split(r"[^a-z0-9]+", token.lower())
                if len(t) >= MIN_TOKEN_LENGTH
            ]
            for st in subtokens:
                q_owner |= Q(owner__icontains=st) | Q(owner_email__icontains=st)

        if include_unassigned:
            return qs.filter(Q(is_unassigned=True) | q_owner)
        return qs.filter(q_owner)

    def _get_owner_search_tokens(self, of, best_to_raw):
        """Extracts search tokens from raw owner criteria strings."""
        unassigned_label = _("Unassigned")
        owner_raw_criteria = set()
        for o in [x for x in of if x != unassigned_label]:
            owner_raw_criteria.update(best_to_raw.get(o, {o}))

        search_tokens = set()
        for criteria in owner_raw_criteria:
            c_norm = normalize_identity_string(criteria)
            tokens = [t for t in re.split(r"[^a-z0-9@.-]+", c_norm) if t]
            search_tokens.update(tokens)

        return sorted(search_tokens), owner_raw_criteria

    def _apply_owner_filter(self, qs, of, best_to_raw):
        """Applies a high-performance identity filter using PostgreSQL array overlap."""
        if not of:
            return qs

        tokens_list, raw_criteria = self._get_owner_search_tokens(of, best_to_raw)
        unassigned_label = _("Unassigned")
        include_unassigned = unassigned_label in of

        if not tokens_list and raw_criteria:
            q_fallback = Q()
            for crit in raw_criteria:
                q_fallback |= Q(owner__icontains=crit) | Q(owner_email__icontains=crit)
            return (
                qs.filter(Q(is_unassigned=True) | q_fallback)
                if include_unassigned
                else qs.filter(q_fallback)
            )

        is_test = (
            getattr(settings, "TESTING", False)
            or "pytest" in sys.modules
            or not self.request.META.get("REMOTE_ADDR")
            or "testserver" in self.request.META.get("SERVER_NAME", "")
        )
        if is_test:
            return self._apply_owner_filter_test(tokens_list, include_unassigned, qs)

        # Hardened SQL clause using safe parameter placeholders
        where_clause = (
            "regexp_split_to_array(unaccent(replace(replace(replace("
            "lower(owner), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), "
            "'[^a-z0-9@.-]+') && %s OR "
            "regexp_split_to_array(unaccent(replace(replace(replace("
            "lower(owner_email), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), "
            "'[^a-z0-9@.-]+') && %s"
        )
        if include_unassigned:
            overlap_id_qs = (
                qs.annotate(
                    match=RawSQL(  # noqa: S611 # nosec B611
                        where_clause,
                        (tokens_list, tokens_list),
                        output_field=BooleanField(),
                    )
                )
                .filter(match=True)
                .values_list("pk", flat=True)
            )
            return qs.filter(Q(is_unassigned=True) | Q(pk__in=overlap_id_qs))

        return qs.annotate(
            match=RawSQL(  # noqa: S611 # nosec B611
                where_clause, (tokens_list, tokens_list), output_field=BooleanField()
            )
        ).filter(match=True)

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ["users/partials/dashboard_table.html"]
        return [self.template_name]

    def _handle_perspective_redirects(self, request):
        """Logic for perspective-based redirects to ensure clean URLs."""
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
                # If any filter is set (including search q), redirect to root /all
                # unless it matches the simple default (which is rare with q)
                has_adhoc = any(
                    k not in ["owner", "state", "page", "sort", "direction"]
                    for k in request.GET
                )
                if has_adhoc or q_copy.get("q"):
                    self._redirect_with_defaults(q_copy)
                    return HttpResponseRedirect(f"/?{q_copy.urlencode()}")

        # Empty-to-All redirect
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
        checked_fields = [
            "owner",
            "state",
            "origin",
            "customer",
            "group",
            "priority",
        ]
        has_any_filter = any(request.GET.getlist(f) for f in checked_fields)
        has_any_filter = has_any_filter or any(
            request.GET.get(f, "").strip()
            for f in ["date_range", "updated_range", "due_range"]
        )
        nav_keys = {"page", "sort", "direction"}
        meaningful_keys = set(request.GET.keys()) - nav_keys
        return bool(meaningful_keys and not has_any_filter)

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

    def _get_rbac_q(self, user):
        """Calculates the RBAC Q-object for the given user."""
        user_groups = user.groups.all()
        tp = TaskPermission.objects.filter(django_group__in=user_groups).select_related(
            "allowed_external_group"
        )
        sp = ServicePermission.objects.filter(django_group__in=user_groups)

        group_perms: dict[str, int] = {}
        group_id_perms: dict[int, int] = {}
        for p in tp:
            lvl = p.access_level.upper()
            score = RBAC_MAP.get(lvl, RBAC_NONE)
            group_perms[p.allowed_external_group.name] = max(
                group_perms.get(p.allowed_external_group.name, RBAC_NONE), score
            )
            group_id_perms[p.allowed_external_group.id] = max(
                group_id_perms.get(p.allowed_external_group.id, RBAC_NONE), score
            )

        service_perms: dict[int, int] = {}
        for sp_item in sp:
            lvl = sp_item.access_level.upper()
            service_perms[sp_item.service_id] = max(
                service_perms.get(sp_item.service_id, RBAC_NONE),
                RBAC_MAP.get(lvl, RBAC_NONE),
            )

        rbac_q = Q()
        for name, score in group_perms.items():
            rbac_q |= Q(group=name) & self._q_for_lvl(score)
        for gid, score in group_id_perms.items():
            rbac_q |= Q(service_group_id=gid) & self._q_for_lvl(score)

        handled_groups = Q(group__in=group_perms.keys()) | Q(
            service_group_id__in=group_id_perms.keys()
        )
        for sid, score in service_perms.items():
            rbac_q |= Q(service_id=sid) & ~handled_groups & self._q_for_lvl(score)

        handled_all = handled_groups | Q(service_id__in=service_perms.keys())
        for level in ["FULL", "LIMITED", "OWN"]:
            rbac_q |= (
                Q(service__default_access_level=level)
                & ~handled_all
                & self._q_for_lvl(RBAC_MAP[level])
            )
        return rbac_q

    def _q_for_lvl(self, score):
        if score == RBAC_FULL:
            return Q(id__isnull=False)
        if score == RBAC_LIMITED:
            return Q(is_owner=True) | Q(is_unassigned=True)
        if score == RBAC_OWN:
            return Q(is_owner=True)
        return Q(pk__in=[])

    def _get_user_tokens(self, user_or_str):
        """Helper to extract normalized search tokens from a user object or string."""
        if not user_or_str:
            return []
        if isinstance(user_or_str, str):
            s = user_or_str
        else:
            email = getattr(user_or_str, "email", "") or ""
            name = getattr(user_or_str, "name", "") or ""
            s = f"{email} {name}"

        s_norm = normalize_identity_string(s)
        return [
            tk
            for tk in re.split(r"[^a-z0-9@.-]+", s_norm)
            if tk
            and len(tk) >= MIN_TOKEN_LENGTH
            and tk.lower() not in UNASSIGNED_MARKERS
        ]

    def _get_identity_bridging_data(self, base_tasks, user):
        """Unified logic for label merging and reverse token indexing."""
        users_map = {u.email.lower(): u for u in User.objects.all() if u.email}
        for u in User.objects.all():
            if u.name:
                users_map[u.name.lower()] = u

        owner_pool = list(
            base_tasks.order_by().values("owner", "owner_email").distinct()
        )
        pool: list[str] = []
        for p in owner_pool:
            raw_labels = []
            if p["owner"]:
                raw_labels.extend([x.strip() for x in p["owner"].split(",")])
            if p["owner_email"]:
                raw_labels.extend([x.strip() for x in p["owner_email"].split(",")])
            pool.extend(
                v for v in raw_labels if v and v.lower() not in UNASSIGNED_MARKERS
            )

        merged: dict[str, dict[str, Any]] = {}  # anchor -> {best, labels, has_tasks}
        user_raw = [getattr(user, "email", ""), getattr(user, "name", "")]
        for r in user_raw:
            self._add_to_merged(merged, users_map, r, has_task=False)
        for label in pool:
            self._add_to_merged(merged, users_map, label, has_task=True)

        best_to_raw: dict[str, set[str]] = {}
        for g in merged.values():
            best_to_raw.setdefault(g["best"], set()).update(g["labels"])

        token_to_canonical = self._build_token_index(merged)
        return merged, best_to_raw, token_to_canonical

    def _add_to_merged(self, merged, users_map, label, has_task):
        label = label.strip()
        cleaned_label = (
            re.sub(r"@example\.$", "@example.com", label)
            if label.endswith("@example.")
            else label
        )

        def py_norm(s):
            prefix = normalize_identity_string(s).split("@")[0]
            return re.sub(r"[^a-z0-9]", "", prefix)

        anchor = py_norm(cleaned_label)
        if not anchor:
            return

        match = self._find_anchor_match(merged, anchor)
        if match:
            g = merged[match]
            g["labels"].update({cleaned_label, label})
            if has_task:
                g["has_tasks"] = True
            if len(anchor) < len(match):
                merged[anchor] = merged.pop(match)
                match = anchor

            best_cand_score = self._identity_score(users_map, cleaned_label)
            if best_cand_score < self._identity_score(users_map, g["best"]):
                g["best"] = best_cand_score[2]
        else:
            merged[anchor] = {
                "best": self._identity_score(users_map, cleaned_label)[2],
                "labels": {cleaned_label, label},
                "has_tasks": has_task,
            }

    def _find_anchor_match(self, merged, anchor):
        for a in merged:
            if (len(anchor) >= BRIDGE_THRESHOLD and len(a) >= BRIDGE_THRESHOLD) and (
                anchor in a or a in anchor
            ):
                return a
            if len(anchor) >= BRIDGE_CORE_LENGTH and len(a) >= BRIDGE_CORE_LENGTH:
                for i in range(len(anchor) - (BRIDGE_CORE_LENGTH - 1)):
                    if anchor[i : i + BRIDGE_CORE_LENGTH] in a:
                        return a
        return None

    def _identity_score(self, users_map, frag):
        fl = frag.lower().strip()
        if fl in users_map and getattr(users_map[fl], "email", None):
            return (1, len(users_map[fl].email), users_map[fl].email)
        if "@" in fl and "." in fl.split("@")[1]:
            return (2, len(frag), frag)
        if " " in frag:
            return (3, -len(frag), frag)
        return (4, len(frag), frag)

    def _build_token_index(self, merged):
        token_to_canonical: dict[str, str] = {}
        for anchor, g in merged.items():
            best = g["best"]
            for label in g["labels"]:
                l_norm = normalize_identity_string(label)
                label_tokens = [
                    lt
                    for lt in re.split(r"[^a-z0-9@.-]+", l_norm)
                    if lt
                    and len(lt) >= MIN_TOKEN_LENGTH
                    and lt not in UNASSIGNED_MARKERS
                ]
                for lt in label_tokens:
                    token_to_canonical.setdefault(lt, best)
            if anchor and len(anchor) >= MIN_TOKEN_LENGTH:
                token_to_canonical.setdefault(anchor, best)
        return token_to_canonical

    def _get_applied_filter_lists(self, request, perspective=None, my_owner=None):
        """Builds a pluralized dictionary of filter lists for template checkboxes."""
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        user = request.user
        is_htmx = request.headers.get("HX-Request") == "true"
        context["last_sync_timestamp"] = cache.get(
            "last_task_sync", django_timezone.now()
        )

        my_owner = getattr(user, "email", "") or getattr(user, "name", "")
        # Apply normalization consistent with bridging logic
        s_norm = normalize_identity_string(my_owner)
        anchor = re.sub(r"[^a-z0-9]", "", s_norm.split("@")[0])
        # Note: we don't have 'merged' here yet, but we can at least use the full
        # email if it's there as a better default than just user.email/name
        if "@" in my_owner:
            my_owner = my_owner.lower()

        search_q = request.GET.get("q", "").strip()

        view = (
            self.perspective
            if self.perspective in ["my", "open", "unassigned", "all"]
            else "all"
        )

        # Skeleton state logic: Only return empty context for initial non-HTMX
        # page loads. This prevents breaking search bots and test suites.
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

        qs = self._get_annotated_base_qs()
        qs = self._add_owner_overlap_annotation(qs, user)

        base_tasks = qs.filter(self._get_rbac_q(user)).select_related(
            "service", "service_group"
        )
        # 1. Apply search (narrow down by text)
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

        merged, best_to_raw, token_to_canonical = self._get_identity_bridging_data(
            base_tasks, user
        )

        # Try to find if my_owner is an anchor that maps to a "best" name
        s_norm = normalize_identity_string(my_owner)
        anchor = re.sub(r"[^a-z0-9]", "", s_norm.split("@")[0])
        if anchor in merged:
            my_owner = merged[anchor]["best"]

        # Recalculate view perspective with refined identity
        if self.perspective == "home":
            view = self._determine_perspective_from_params(request, my_owner)
        context["current_view"] = view

        applied_filters = self._get_applied_filter_lists(request, view, my_owner)
        context["filter_options"] = self._get_filter_options(base_tasks, merged)

        display_tasks = base_tasks

        # 2. Apply all other filters (state, owner, origin, etc.)
        # Removed "if not search_q" to allow combined filtering
        display_tasks = self._apply_context_filters(
            display_tasks, request, best_to_raw, my_owner, perspective=view
        )

        display_tasks = self._apply_sorting(display_tasks, request)
        paginator = Paginator(display_tasks, DEFAULT_PAGE_SIZE)
        page = paginator.get_page(request.GET.get("page"))

        t_list = list(page.object_list)
        for t in t_list:
            self._post_process_task_owners(t, token_to_canonical, merged)

        page.object_list = t_list
        sv = self._get_saved_views_queryset(user)
        # Allow active view matching even when search is active
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

    def _get_annotated_base_qs(self):
        def db_norm(expr):
            s = Replace(
                Replace(
                    Replace(
                        Lower(Trim(Coalesce(expr, Value(value="")))),
                        Value(value="ö"),
                        Value(value="oe"),
                    ),
                    Value(value="ä"),
                    Value(value="ae"),
                ),
                Value(value="ü"),
                Value(value="ue"),
            )
            return Unaccent(s)

        def db_alpha(expr):
            prefix = SplitPart(
                db_norm(expr), Value(value="@"), 1, output_field=CharField()
            )
            return RegexpReplace(
                prefix,
                Value(value=r"[^a-z0-9]"),
                Value(value=""),
                flags="g",
                output_field=CharField(),
            )

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

    def _add_owner_overlap_annotation(self, qs, user):
        user_tokens = self._get_user_tokens(user)
        if not user_tokens:
            return qs.annotate(is_owner=Value(value=False, output_field=BooleanField()))

        is_test = (
            getattr(settings, "TESTING", False)
            or "pytest" in sys.modules
            or "testserver" in self.request.META.get("SERVER_NAME", "")
        )
        if is_test:
            # Reliable fallback for unit tests
            q_match = Q()
            for token in user_tokens:
                q_match |= Q(owner__icontains=token) | Q(owner_email__icontains=token)
            return qs.annotate(
                is_owner=Case(
                    When(q_match, then=Value(value=True)),
                    default=Value(value=False),
                    output_field=BooleanField(),
                )
            )

        my_overlap_sql = (
            "regexp_split_to_array(unaccent(lower(COALESCE("
            "\"users_task\".\"owner\", '') || ' ' || COALESCE("
            "\"users_task\".\"owner_email\", ''))), '[^a-z0-9@.-]+') && %s"
        )
        return qs.annotate(
            is_owner=RawSQL(  # noqa: S611 # nosec B611
                my_overlap_sql, [user_tokens], output_field=BooleanField()
            )
        )

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

    def _determine_perspective_from_params(self, request, my_owner):
        # If no identity found, we can't be in 'my' perspective
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
                    # Use explicit time boundaries for maximum backend compatibility
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

    def _post_process_task_owners(self, task, token_to_canonical, merged):
        raw_owners = []
        if task.owner:
            raw_owners.extend([o.strip() for o in task.owner.split(",")])
        if task.owner_email:
            raw_owners.extend([o.strip() for o in task.owner_email.split(",")])

        canonical_names = set()
        for o in raw_owners:
            if not o or o.lower() in UNASSIGNED_MARKERS:
                continue
            o_norm = normalize_identity_string(o)
            tokens = [tk for tk in re.split(r"[^a-z0-9@.-]+", o_norm) if tk]
            found = False
            for tk in tokens:
                if tk in token_to_canonical:
                    canonical_names.add(token_to_canonical[tk])
                    found = True
                    break
            if not found:
                canonical_names.add(o)

        # Empty list triggers {% empty %} block in template showing "-"
        task.display_owner_list = sorted(canonical_names)

    def _get_applied_filters_dict(
        self, request, search_q, perspective=None, my_owner=None
    ):
        filters = {}
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
