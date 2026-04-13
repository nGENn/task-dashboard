import json
import logging
import re
import time
import unicodedata

from django.core.cache import cache
from django.utils import timezone as django_timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    F,
    Func,
    Q,
    QuerySet,
    Value,
    When,
)
from django.db.models.expressions import RawSQL
from django.db.models.functions import Lower, Replace, Trim
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, UpdateView

from task_dashboard.users.tasks import fetch_all_tasks_task, fetch_service_tasks

from task_dashboard.users.models import (
    GlobalSetting,
    SavedView,
    ServicePermission,
    Task,
    TaskPermission,
    User,
    compare_query_params,
)

logger = logging.getLogger(__name__)

# --- POSTGRES HELPER FUNCTIONS ---
class Unaccent(Func):
    function = "UNACCENT"

class SplitPart(Func):
    function = "SPLIT_PART"

class RegexpReplace(Func):
    function = "REGEXP_REPLACE"

class StringToArray(Func):
    function = "STRING_TO_ARRAY"

class Unnest(Func):
    function = "UNNEST"

def normalize_identity_string(s):
    if not s:
        return ""
    s_norm = str(s).lower().strip().replace("ö", "oe").replace("ä", "ae").replace("ü", "ue")
    return unicodedata.normalize('NFKD', s_norm).encode('ASCII', 'ignore').decode('utf-8')

# --- UTILITY VIEWS ---

class UserUpdateView(UpdateView):
    model = User
    fields = ["name"]
    def get_success_url(self): return reverse("home")
    def get_object(self): return User.objects.get(pk=self.request.user.pk)
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
    fetch_all_tasks_task()
    return HttpResponseRedirect(reverse("home"))

@login_required
def refresh_single_task_view(request, pk):
    task = get_object_or_404(Task, pk=pk)
    fetch_service_tasks(task.service.id)
    return HttpResponseRedirect(reverse("home"))

# --- MAIN DASHBOARD VIEW ---

class DashboardView(TemplateView):
    template_name = "users/dashboard.html"
    perspective = None

    def _apply_owner_filter(self, qs, of, best_to_raw):
        """
        Applies a high-performance identity filter using PostgreSQL array overlap.

        The 'Bag-of-Words' logic:
        1. Takes a list of canonical owner labels (e.g., 'alice.alpha@example.com').
        2. Maps these back to all associated raw identity tokens (email parts, full names).
        3. Tokenizes both the search criteria and the database owner fields.
        4. Uses the Postgres '&&' operator to find any overlap between the search tokens
           and the indexed identity array of the task.
        5. Performs umlaut transliteration (ö -> oe, etc.) and unaccenting to ensure
           matches across fragmented source systems (e.g. Zammad vs GitLab).
        """
        if not of: return qs
        
        owner_raw_criteria = set()
        include_unassigned = _("Unassigned") in of
        for o in [x for x in of if x != _("Unassigned")]:
            owner_raw_criteria.update(best_to_raw.get(o, {o}))
        
        if owner_raw_criteria:
            search_tokens = set()
            for criteria in owner_raw_criteria:
                c_norm = normalize_identity_string(criteria)
                tokens = [t for t in re.split(r'[^a-z0-9@.-]+', c_norm) if t]
                search_tokens.update(tokens)
            
            if search_tokens:
                tokens_list = sorted(search_tokens)
                where_clause = """
                    regexp_split_to_array(unaccent(replace(replace(replace(lower(owner), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), '[^a-z0-9@.-]+') && %s OR
                    regexp_split_to_array(unaccent(replace(replace(replace(lower(owner_email), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), '[^a-z0-9@.-]+') && %s
                """
                if include_unassigned:
                    return qs.filter(Q(is_unassigned=True) | Q(pk__in=qs.extra(where=[where_clause], params=[tokens_list, tokens_list])))
                return qs.extra(where=[where_clause], params=[tokens_list, tokens_list])

        return qs.filter(is_unassigned=True) if include_unassigned else qs

    def get_template_names(self):
        # HTMX requests targeting #task-results should receive only the table partial,
        # not the full dashboard page (which would nest sidebar/navbar inside the table).
        if self.request.headers.get("HX-Request") == "true":
            return ["users/partials/dashboard_table.html"]
        return [self.template_name]

    def get(self, request, *args, **kwargs):
        # === GLOBAL SANITIZER: Strip any rogue ?view= parameter immediately. ===
        # This is a catch-all for old bookmarks or stale links. If view= is present
        # in the URL for ANY perspective, redirect to the exact same URL without it.
        if "view" in request.GET:
            q = request.GET.copy()
            q.pop("view", None)
            base = request.path  # preserves /all, /my, /unassigned, /
            redirect_url = f"{base}?{q.urlencode()}" if q else base
            return HttpResponseRedirect(redirect_url)

        if self.perspective == "home" and not request.GET:
            return HttpResponseRedirect("/my")

        if self.perspective == "all":
            if request.GET:
                meaningful_keys = set(request.GET.keys()) - {"page", "sort", "direction"}
                if meaningful_keys:
                    return HttpResponseRedirect(f"/?{request.GET.urlencode()}")

        if self.perspective in ["my", "unassigned"]:
            # If we are on a named route but have ANY query params submitted
            # and owner is missing, the user cleared their explicit owner filter.
            if request.GET:
                meaningful_keys = set(request.GET.keys()) - {"page", "sort", "direction"}
                if meaningful_keys:
                    q = request.GET.copy()
                    if not q.getlist("owner"):
                        return HttpResponseRedirect(f"/?{q.urlencode()}")

                    # If they have non-standard ad-hoc filters but still retain the owner,
                    # drop to root URL `/?...` so the pure named tab isn't anomalously applied.
                    has_adhoc = False
                    for key in request.GET:
                        if key not in ["owner", "state", "page", "sort", "direction"]:
                            has_adhoc = True
                            break
                    
                    if has_adhoc:
                        if not q.getlist("state"):
                            states = getattr(GlobalSetting.load(), "default_task_states", "open,pending").strip().split(",")
                            q.setlist("state", [s.strip() for s in states if s.strip()])
                        
                        return HttpResponseRedirect(f"/?{q.urlencode()}")

        # === EMPTY-TO-ALL REDIRECT ===
        # When the user submits a filter (any non-nav GET params exist) but every
        # meaningful filter list is empty and there's no search query, treat it as
        # "show everything" and redirect to /all.  Trigger: /my with all owners
        # unchecked → Apply → /all.  Not triggered on /all itself (no infinite loop).
        if request.GET and self.perspective != "all":
            has_search = bool(request.GET.get("q", "").strip())
            if not has_search:
                owner_vals = request.GET.getlist("owner")
                state_vals = request.GET.getlist("state")
                origin_vals = request.GET.getlist("origin")
                customer_vals = request.GET.getlist("customer")
                group_vals = request.GET.getlist("group")
                priority_vals = request.GET.getlist("priority")
                date_val = request.GET.get("date_range", "")
                updated_val = request.GET.get("updated_range", "")
                due_val = request.GET.get("due_range", "")
                has_any_filter = any([
                    owner_vals, state_vals, origin_vals, customer_vals,
                    group_vals, priority_vals, date_val, updated_val, due_val,
                ])
                # Only redirect when there are non-nav keys present but none are filters
                meaningful_keys = set(request.GET.keys()) - {"page", "sort", "direction"}
                if meaningful_keys and not has_any_filter:
                    return HttpResponseRedirect("/all")

        # === ASYNC SKELETON LOADING: Immediate Shell Return ===
        if request.headers.get("HX-Request") != "true":
            # Return templates/users/dashboard.html shell immediately.
            # get_context_data handles the "light" context when HX-Request is missing.
            context = self.get_context_data(**kwargs)
            return self.render_to_response(context)

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):  # noqa: C901, PLR0912, PLR0915
        context = super().get_context_data(**kwargs)
        request = self.request
        user = request.user

        # Identify if this is the deep-fetch (HTMX) or the initial shell
        # Standard HX-Request header check
        is_htmx = request.headers.get("HX-Request") == "true"
        context["last_sync_timestamp"] = cache.get("last_task_sync", django_timezone.now())

        if not is_htmx:
            # Fast-path for initial page load shell
            view = self.perspective if self.perspective in ["my", "unassigned", "all"] else "all"
            context.update({
                "tasks": [],
                "is_htmx_oob": False,
                "current_view": view,
                "applied_filters": {},
                "filter_options": {},
                "stats": {},
                "default_views": [
                    {"name": _("My Tasks"), "view_param": "my", "url": "/my", "description": _("Tasks assigned to you.")},
                    {"name": _("Unassigned"), "view_param": "unassigned", "url": "/unassigned", "description": _("Tasks without an owner.")},
                ],
                "saved_views": [
                    {"id": v.id, "name": v.name, "url": f"/?{v.get_query_string()}", "is_active": False}
                    for v in SavedView.objects.filter(user=user)
                ],
            })
            return context

        t_start = time.monotonic()

        # 1. HELPERS & IDENTITY BASES
        unassigned_markers = ["", "-", "none", "unassigned", "0", "null", "unassigned person"]
        def db_norm(expr):
            # Explicit Transliteration for umlauts before unaccenting
            s = Replace(Replace(Replace(Lower(Trim(expr)), Value("ö"), Value("oe")), Value("ä"), Value("ae")), Value("ü"), Value("ue"))
            return Unaccent(s)

        def db_alpha(expr):
            prefix = SplitPart(db_norm(expr), Value("@"), 1, output_field=CharField())
            return RegexpReplace(prefix, Value(r"[^a-z0-9]"), Value(""), flags="g", output_field=CharField())

        users_map = {u.email.lower(): u for u in User.objects.all() if u.email}
        # Extend users_map with lowercase names for broader matching
        for u in User.objects.all():
            if u.name: users_map[u.name.lower()] = u

        # 2. BASE QUERYSET (RBAC + SEARCH)
        qs = Task.objects.filter(service__is_active=True).annotate(
            owner_base=db_alpha(F("owner")), email_base=db_alpha(F("owner_email")),
            onorm=db_norm(F("owner")), enorm=db_norm(F("owner_email")),
        ).annotate(
            is_unassigned=Case(When(Q(onorm__in=unassigned_markers) & Q(enorm__in=unassigned_markers), then=Value(True)), default=Value(False), output_field=BooleanField()),
        )

        user_raw = [getattr(user, "email", ""), getattr(user, "name", "")]
        my_owner_str = getattr(user, "email", "") or getattr(user, "name", "")
        my_tokens = set()
        o_norm = normalize_identity_string(my_owner_str)
        for tk in re.split(r'[^a-z0-9@.-]+', o_norm):
            if len(tk) >= 3 and tk not in unassigned_markers: my_tokens.add(tk)
            
        if my_tokens:
            tokens_list = list(my_tokens)
            my_overlap_sql = "regexp_split_to_array(unaccent(lower(COALESCE(\"users_task\".\"owner\", '') || ' ' || COALESCE(\"users_task\".\"owner_email\", ''))), '[^a-z0-9@.-]+') && %s"
            params = [tokens_list]
        else:
            my_overlap_sql = "FALSE"
            params = []

        qs = qs.annotate(is_owner=RawSQL(my_overlap_sql, params, output_field=BooleanField()))

        # RBAC Calculation
        user_groups = user.groups.all()
        tp = TaskPermission.objects.filter(django_group__in=user_groups).select_related("allowed_external_group")
        sp = ServicePermission.objects.filter(django_group__in=user_groups)
        p_map = {"NONE": 0, "OWN": 1, "LIMITED": 2, "FULL": 3}
        group_perms = {}
        group_id_perms = {}
        for p in tp:
            score = p_map.get(p.access_level.upper(), 0)
            group_perms[p.allowed_external_group.name] = max(group_perms.get(p.allowed_external_group.name, 0), score)
            group_id_perms[p.allowed_external_group.id] = max(group_id_perms.get(p.allowed_external_group.id, 0), score)
        service_perms = {}
        for p in sp:
            service_perms[p.service_id] = max(service_perms.get(p.service_id, 0), p_map.get(p.access_level.upper(), 0))

        def q_for_lvl(score):
            if score == 3: return Q(id__isnull=False)
            if score == 2: return Q(is_owner=True) | Q(is_unassigned=True)
            if score == 1: return Q(is_owner=True)
            return Q(pk__in=[])

        rbac_q = Q()
        for name, score in group_perms.items(): rbac_q |= Q(group=name) & q_for_lvl(score)
        for gid, score in group_id_perms.items(): rbac_q |= Q(service_group_id=gid) & q_for_lvl(score)
        handled_groups = Q(group__in=group_perms.keys()) | Q(service_group_id__in=group_id_perms.keys())
        for sid, score in service_perms.items(): rbac_q |= Q(service_id=sid) & ~handled_groups & q_for_lvl(score)
        handled_all = handled_groups | Q(service_id__in=service_perms.keys())
        for level in ["FULL", "LIMITED", "OWN"]:
            rbac_q |= Q(service__default_access_level=level) & ~handled_all & q_for_lvl(p_map[level])

        base_tasks = qs.filter(rbac_q).select_related("service", "service_group")
        
        # Apply Search (q) to the Base Universe
        search_q = request.GET.get("q", "").strip()
        if search_q: base_tasks = base_tasks.filter(search_text__icontains=search_q)

        # 3. GLOBAL TRACK (STATS & FILTERS)
        context["stats"] = base_tasks.aggregate(
            total=Count("id", distinct=True),
            my_tasks=Count("id", filter=Q(is_owner=True, status__in=["open", "pending", "new"]), distinct=True),
            open=Count("id", filter=Q(status="open"), distinct=True),
            pending=Count("id", filter=Q(status="pending"), distinct=True),
            resolved=Count("id", filter=Q(status="resolved"), distinct=True),
        )

        def get_opts(qs, field): return sorted(list(qs.order_by().values_list(field, flat=True).distinct()))

        # Identity Merging for Owners dropdown
        owner_pool = list(base_tasks.order_by().values("owner", "owner_email").distinct())
        pool = []
        for p in owner_pool:
            # Handle comma-separated strings
            raw_labels = []
            if p["owner"]: raw_labels.extend([x.strip() for x in p["owner"].split(",")])
            if p["owner_email"]: raw_labels.extend([x.strip() for x in p["owner_email"].split(",")])
            for v in raw_labels:
                if v and v.lower() not in unassigned_markers: pool.append(v)
        
        merged = {} # anchor -> {best, labels, has_tasks}
        def add_to_merged(label, has_task):
            label = label.strip()
            # Clean dot-truncated emails like delta@example. -> delta@example.com
            cleaned_label = re.sub(r"@example\.$", "@example.com", label) if label.endswith("@example.") else label
            # Anchor normalization using Python version of db_norm logic for consistency
            def py_norm(s):
                s_norm = normalize_identity_string(s)
                # Strip non-alphanumeric from the local part (before @)
                prefix = s_norm.split("@")[0]
                return re.sub(r"[^a-z0-9]", "", prefix)

            anchor = py_norm(cleaned_label)
            if not anchor: return

            # Hardened Bridging: Match if anchors share a significant common substring (5+)
            # OR if one is a substring of the other (4+)
            def r_score(frag):
                fl = frag.lower().strip()
                if fl in users_map and getattr(users_map[fl], "email", None):
                    return (1, len(users_map[fl].email), users_map[fl].email)
                if "@" in fl and "." in fl.split("@")[1]:
                    return (2, len(frag), frag)
                if " " in frag:
                    return (3, -len(frag), frag)
                return (4, len(frag), frag)

            match = None
            for a in merged:
                # 1. Simple subset check (legacy logic)
                if (len(anchor) >= 4 and len(a) >= 4) and (anchor in a or a in anchor):
                    match = a
                    break
                # 2. Aggressive overlapping core (e.g. jmessling and jonamessling share 'messling')
                # We look for a common substring of at least 5 characters.
                if len(anchor) >= 5 and len(a) >= 5:
                    # Very simple common substring check for standard last names
                    core_found = False
                    for i in range(len(anchor) - 4):
                        if anchor[i:i+5] in a:
                            core_found = True
                            break
                    if core_found:
                        match = a
                        break
            
            if match:
                g = merged[match]
                g["labels"].add(cleaned_label)
                if label != cleaned_label: g["labels"].add(label)
                if has_task: g["has_tasks"] = True
                # Keep the shortest anchor as the dictionary key to maximize future bridging
                if len(anchor) < len(match): merged[anchor] = merged.pop(match); match = anchor

                existing_best = g["best"]
                best_cand_score = r_score(cleaned_label)
                existing_score = r_score(existing_best)
                if best_cand_score < existing_score:
                    g["best"] = best_cand_score[2]
            else:
                best_val = r_score(cleaned_label)[2]
                merged[anchor] = {"best": best_val, "labels": {cleaned_label, label}, "has_tasks": has_task}

        for r in user_raw: add_to_merged(r, False)
        for label in pool: add_to_merged(label, True)
        
        # Mapping selected best labels back to ALL associated raw criteria for robust filtering
        best_to_raw = {}
        for g in merged.values():
            best_to_raw.setdefault(g["best"], set()).update(g["labels"])

        p_weights = {"critical": 1, "high": 2, "medium": 3, "low": 4}

        context["filter_options"] = {
            "customers": get_opts(base_tasks, "customer"),
            "groups": get_opts(base_tasks, "group"),
            "origins": get_opts(base_tasks, "service__name"),
            "states": get_opts(base_tasks, "status"),
            "priorities": sorted(get_opts(base_tasks, "priority"), key=lambda x: p_weights.get(x.lower(), 5)),
            "owners": [_("Unassigned")] + sorted(list({g["best"] for g in merged.values()}), key=str.lower),
        }

        # 4. TABLE TRACK (PAGINATION & ROWS)
        display_tasks = base_tasks
        
        my_owner = getattr(user, "email", "") or getattr(user, "name", "")
        
        # Determine current view for tab highlighting
        view = self.perspective if self.perspective in ["my", "unassigned", "all"] else "all"

        if self.perspective == "home" and not search_q:
            # Check if current params match "My Tasks" or "Unassigned" defaults
            # This ensures that / redirects highlight the correct tab
            default_states = sorted([s.strip() for s in GlobalSetting.load().default_task_states.split(",") if s.strip()])
            
            my_params = {"owner": [my_owner]} if my_owner else {}
            my_params["state"] = default_states
            
            unassigned_params = {"owner": ["Unassigned"], "state": default_states}
            
            if compare_query_params(request.GET, my_params):
                view = "my"
            elif compare_query_params(request.GET, unassigned_params):
                view = "unassigned"
            else:
                view = "all" # Ad-hoc parameters on root URL always light up "All Tasks"
        elif search_q:
            view = "all"

        context["current_view"] = view

        # Global Search: when q is active, bypass all filters (state, owner, etc.)
        # so that closed/resolved tasks are still findable.
        if not search_q:
            # Determine states implicitly if hitting a named route
            st = request.GET.getlist("state")
            if not st and self.perspective != "all" and (self.perspective in ["my", "unassigned"] or not request.GET):
                st = [s.strip() for s in GlobalSetting.load().default_task_states.split(",") if s.strip()]
            
            if st: display_tasks = display_tasks.filter(status__in=st)
            
            of = request.GET.getlist("owner")
            if not of:
                if self.perspective == "my" and my_owner: of = [my_owner]
                elif self.perspective == "unassigned": of = [_("Unassigned")]

            if self.perspective == "all" and not of:
                # Bypass _apply_owner_filter entirely for /all without owner filters
                pass
            else:
                display_tasks = self._apply_owner_filter(display_tasks, of, best_to_raw)

            def apply_m(qs, p, f):
                vals = request.GET.getlist(p)
                return qs.filter(**{f"{f}__in": vals}) if vals else qs
            display_tasks = apply_m(display_tasks, "origin", "service__name")
            display_tasks = apply_m(display_tasks, "customer", "customer")
            display_tasks = apply_m(display_tasks, "group", "group")
            display_tasks = apply_m(display_tasks, "priority", "priority")

            def apply_dr(qs, p, f):
                dr = request.GET.get(p, "").strip()
                if not dr:
                    return qs
                if " to " in dr:
                    try:
                        parts = dr.split(" to ")
                        if len(parts) == 2 and parts[1]:
                            return qs.filter(**{f"{f}__date__range": [parts[0], parts[1]]})
                        # If " to " exists but second part is missing, fall back to first part
                        dr = parts[0]
                    except Exception:
                        pass

                # Handle single date or fallback from partial range
                if re.match(r"^\d{4}-\d{2}-\d{2}$", dr):
                    return qs.filter(**{f"{f}__date": dr})
                return qs
            display_tasks = apply_dr(display_tasks, "date_range", "created_at")
            display_tasks = apply_dr(display_tasks, "updated_range", "updated_at")
            display_tasks = apply_dr(display_tasks, "due_range", "due_date")
        else:
            st, of = [], []

        display_tasks = display_tasks.distinct()
        display_tasks = display_tasks.annotate(priority_rank=Case(When(priority__iexact="critical", then=Value("0")), When(priority__iexact="high", then=Value("1")), When(priority__iexact="medium", then=Value("2")), When(priority__iexact="normal", then=Value("2")), When(priority__iexact="low", then=Value("3")), default=Value("4"), output_field=CharField()))
        s_f = {"origin": "service__name", "id": "external_id", "status": "status", "priority": "priority_rank", "title": "title", "customer": "customer", "group": "group", "owner": "owner_email", "created_at": "created_at", "updated_at": "updated_at", "due_date": "due_date"}.get(request.GET.get("sort"), "updated_at")
        display_tasks = display_tasks.order_by(F(s_f).desc(nulls_last=True) if request.GET.get("direction", "desc") == "desc" else F(s_f).asc(nulls_first=True))

        paginator = Paginator(display_tasks, 50)
        page = paginator.get_page(request.GET.get("page"))
        
        # 5. POST-PROCESSING (Unified labels & Regex Repair)
        # Build a direct token -> canonical label reverse-index from all merged groups.
        # This ensures first-name tokens (e.g. "alice") resolve to the canonical email
        # (e.g. "alice.alpha@example.com") because "Alice Alpha" is a known label in that group.
        token_to_canonical = {}
        for _anchor, g in merged.items():
            best = g["best"]
            for label in g["labels"]:
                l_norm = normalize_identity_string(label)
                label_tokens = [lt for lt in re.split(r'[^a-z0-9@.-]+', l_norm) if lt and len(lt) >= 3 and lt not in unassigned_markers]
                for lt in label_tokens:
                    if lt not in token_to_canonical:
                        token_to_canonical[lt] = best
            # Also map the anchor itself
            if _anchor and len(_anchor) >= 3:
                token_to_canonical[_anchor] = best

        t_list = list(page.object_list)
        for t in t_list:
            clean = set()
            raw_string = f"{t.owner or ''} {t.owner_email or ''}"
            if raw_string.strip():
                c_norm = normalize_identity_string(raw_string)
                tokens = [tk for tk in re.split(r'[^a-z0-9@.-]+', c_norm) if tk]
                
                for r in tokens:
                    if not r or r in unassigned_markers: continue
                    r = re.sub(r"@example\.$", "@example.com", r)
                    
                    # Primary: direct token lookup
                    canonical = token_to_canonical.get(r)
                    if canonical:
                        clean.add(canonical)
                        continue
                    
                    # Fallback: anchor-based substring/overlap matching
                    base = re.sub(r"[^a-z0-9]", "", r.split("@")[0])
                    match = r
                    if base:
                        for b, g in merged.items():
                            if (len(base) >= 4 and len(b) >= 4) and (base in b or b in base): match = g["best"]; break
                            elif len(base) >= 5 and len(b) >= 5:
                                core_found = False
                                for i in range(len(base) - 4):
                                    if base[i:i+5] in b: core_found = True; break
                                if core_found: match = g["best"]; break
                    clean.add(match)
            
            clean = sorted(list(clean), key=str.lower)
            t.display_owner_list = clean
            t.display_owner = ", ".join(clean) if clean else ""
            if clean and len(clean) == 1: t.owner_email = clean[0]; t.owner = ""

        page.object_list = t_list
        sv = SavedView.objects.filter(user=user)
        # Don't highlight any saved view when search is active
        active_id = None if search_q else next((v.id for v in sv if v.matches_params(request.GET)), None)

        # Flag for OOB swaps: when HTMX fetches the table partial,
        # also push updated stats and tabs via hx-swap-oob.
        is_htmx = request.headers.get("HX-Request") == "true"

        # Telemetry: use max service latency from health checks (injected by context processor)
        # instead of page load speed — the navbar badge should reflect the slowest service.

        # Zero-Ghost Search: when q is active, report empty applied_filters
        # so all filter checkboxes uncheck themselves.
        # Sanitization: Ensure 'view' is never in applied_filters to keep URLs clean.
        if search_q:
            applied_filters = {
                "states": [], "owners": [], "q": search_q,
                "origins": [], "customers": [], "groups": [], "priorities": [],
            }
        else:
            applied_filters = {
                "states": st, "owners": of, "q": search_q,
                "origins": request.GET.getlist("origin"),
                "customers": request.GET.getlist("customer"),
                "groups": request.GET.getlist("group"),
                "priorities": request.GET.getlist("priority"),
            }
        
        applied_filters.pop("view", None)

        context.update({
            "tasks": page, "page_obj": page, "custom_page_range": paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1),
            "saved_views": [{"id": v.id, "name": v.name, "url": f"/?{v.get_query_string()}", "is_active": (v.id == active_id)} for v in sv],
            "active_saved_view_id": active_id,
            "applied_filters": applied_filters,
            "default_views": [
                {"name": _("My Tasks"), "view_param": "my", "url": "/my", "description": _("Tasks assigned to you.")},
                {"name": _("Unassigned"), "view_param": "unassigned", "url": "/unassigned", "description": _("Tasks without an owner.")},
            ],
            "is_htmx_oob": is_htmx,
        })
        return context

@login_required
@require_POST
def save_view(request):
    try:
        data = json.loads(request.body)
        name, qp = data.get("name"), data.get("query_params", {})
        if not name: return JsonResponse({"error": _("Name is required")}, status=400)
        v, c = SavedView.objects.update_or_create(user=request.user, name=name, defaults={"query_params": qp})
        return JsonResponse({"status": "success", "id": v.id, "created": c})
    except: return JsonResponse({"error": _("Internal server error")}, status=500)

@login_required
@require_POST
def delete_saved_view(request, pk):
    get_object_or_404(SavedView, pk=pk, user=request.user).delete()
    return HttpResponseRedirect(reverse("home"))
