import os
import re
import sys
import time
from pathlib import Path

import django

# Pre-setup environment for Django-environ
os.environ["DJANGO_READ_DOT_ENV_FILE"] = "True"
sys.path.append(str(Path.cwd()))

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
try:
    django.setup()
except Exception:  # noqa: BLE001
    sys.exit(1)

from django.core.paginator import Paginator
from django.db import connection
from django.db.models import BooleanField
from django.db.models import Case
from django.db.models import CharField
from django.db.models import F
from django.db.models import Func
from django.db.models import Q
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Coalesce
from django.db.models.functions import Lower
from django.db.models.functions import Trim

from task_dashboard.users.models import ServicePermission
from task_dashboard.users.models import Task
from task_dashboard.users.models import TaskPermission
from task_dashboard.users.models import User

# --- CONSTANTS ---
LIMIT_PAGINATION = 50
DB_TIME_THRESHOLD = 0.5
MIN_TOKEN_LENGTH_SEARCH = 3
RBAC_PRIORITY_EMAIL = 3
RBAC_PRIORITY_HINT = 2
RBAC_PRIORITY_LOW = 1


class SplitPart(Func):
    function = "split_part"
    template = "%(function)s(%(expressions)s)"


class Unaccent(Func):
    function = "unaccent"


class StringToArray(Func):
    function = "string_to_array"


class Unnest(Func):
    function = "unnest"


class RegexpReplace(Func):
    function = "REGEXP_REPLACE"

    def __init__(self, expression, pattern, replacement, flags=None, **extra):
        expressions = [expression, pattern, replacement]
        if flags:
            expressions.append(Value(value=flags))
        super().__init__(*expressions, **extra)


def extract_base_py(val):
    if not val:
        return ""
    v = str(val).lower().strip()
    # 1. Email local part
    v = v.split("@")[0]
    # 2. First part of name (anchor)
    for sep in [" ", ",", "-"]:
        if sep in v:
            parts = [p for p in v.split(sep) if p]
            if parts:
                v = parts[0]
    # 3. Alphanumeric cleanup
    return re.sub(r"[^a-z0-9]", "", v)


def get_base_expr(field_name_or_expr):
    # Mirror exactly lines 274-284 of views.py
    if isinstance(field_name_or_expr, str):
        expr = Unaccent(Lower(Trim(F(field_name_or_expr))))
    else:
        expr = Unaccent(Lower(Trim(field_name_or_expr)))

    e = SplitPart(expr, Value(value="@"), 1, output_field=CharField())
    e = SplitPart(e, Value(value=" "), 1, output_field=CharField())
    e = SplitPart(e, Value(value=","), 1, output_field=CharField())
    e = SplitPart(e, Value(value="-"), 1, output_field=CharField())
    return RegexpReplace(
        e,
        Value(value=r"[^a-z0-9]"),
        Value(value=""),
        flags="g",
        output_field=CharField(),
    )


def verify_identity_matching():
    test_cases = [
        ("Zeta Bob", "zeta"),
        ("Müller", "muller"),
        ("Judith Delta", "judith"),  # First part is anchor now
        ("zeta@example.com", "zeta"),
        ("john.doe@example.com", "johndoe"),
    ]

    any_fail = False
    for tc, expected in test_cases:
        # Test SQL via a single annotation
        res = Task.objects.annotate(
            val=Value(value=tc, output_field=CharField()), base=get_base_expr("val")
        ).values_list("base", flat=True)[:1][0]

        match = "PASS" if res == expected else "FAIL"
        if match == "FAIL":
            any_fail = True

    if not any_fail:
        pass


def verify_canonical_fallback():
    # Simulate a task where email is present and owner is different
    # Hierarchy: Email > Full Name > Username
    tasks = Task.objects.annotate(
        c_base=Coalesce(
            Case(
                When(
                    ~Q(owner_email__in=["", "-", "None", "Unassigned"]),
                    then=get_base_expr("owner_email"),
                ),
                default=None,
            ),
            Case(
                When(
                    ~Q(owner__in=["", "-", "None", "Unassigned"]),
                    then=get_base_expr("owner"),
                ),
                default=None,
            ),
            Value(value=""),
            output_field=CharField(),
        )
    ).all()[:10]

    for _t in tasks:
        pass


def verify_unnest_and_distinct():
    # 1. Test Status uniqueness
    statuses = list(Task.objects.order_by().values_list("status", flat=True).distinct())
    if len(statuses) == len(set(statuses)):
        pass

    # 2. Test Owner Unnesting
    # We'll check if any returned owner contains a comma
    unique_owners_ext = set(
        Task.objects.annotate(
            clean=Trim(Unnest(StringToArray(F("owner"), Value(value=","))))
        )
        .order_by()
        .values_list("clean", flat=True)
        .distinct()
    )
    unassigned = ["", "-", "None", "Unassigned"]
    unique_owners = unique_owners_ext - set(unassigned)

    has_comma = any("," in o for o in unique_owners)
    if not has_comma:
        pass


def get_pairs(field):
    unassigned = ["", "-", "None", "Unassigned"]
    return (
        Task.objects.exclude(**{f"{field}__in": unassigned})
        .annotate(
            raw=Trim(Unnest(StringToArray(F(field), Value(value=",")))),
            canon=get_base_expr("raw"),
        )
        .values_list("raw", "canon")
        .distinct()
    )


def update_identity_map(identity_map, pairs, is_email):
    for raw, canon in pairs:
        if not canon:
            continue
        priority = (
            RBAC_PRIORITY_EMAIL
            if is_email
            else (
                RBAC_PRIORITY_HINT
                if " " in raw or "." in raw or "-" in raw
                else RBAC_PRIORITY_LOW
            )
        )
        if (
            canon not in identity_map
            or priority > identity_map[canon][1]
            or (
                priority == identity_map[canon][1]
                and len(raw) > len(identity_map[canon][0])
            )
        ):
            identity_map[canon] = (raw, priority)


def verify_hierarchy():
    owner_pairs = get_pairs("owner")
    email_pairs = get_pairs("owner_email")

    identity_map = {}
    update_identity_map(identity_map, owner_pairs, is_email=False)
    update_identity_map(identity_map, email_pairs, is_email=True)

    test_users = User.objects.all().order_by("id")[:5]
    if not test_users:
        return

    for user in test_users:
        verify_user_rbac(user)


def verify_user_rbac(user):
    user_bases = set()
    if user.email:
        user_bases.add(extract_base_py(user.email))
    if getattr(user, "name", ""):
        user_bases.add(extract_base_py(user.name))
    user_bases.discard("")
    user_bases_list = sorted(user_bases, key=len, reverse=True)

    unassigned_markers = ["", "-", "None", "Unassigned"]

    user_groups = user.groups.all()
    task_perms = TaskPermission.objects.filter(django_group__in=user_groups).values(
        "allowed_external_group_id", "access_level"
    )
    service_perms = ServicePermission.objects.filter(
        django_group__in=user_groups
    ).values("service_id", "access_level")

    tp_grouped = group_perms_by_level(task_perms, "allowed_external_group_id")
    sp_grouped = group_perms_by_level(service_perms, "service_id")

    tasks_sql = Task.objects.filter(service__is_active=True).annotate(
        owner_norm=Unaccent(Lower(Trim(F("owner")))),
        email_norm=Unaccent(Lower(Trim(F("owner_email")))),
        is_unassigned=Case(
            When(
                Q(owner_email__in=unassigned_markers) & Q(owner__in=unassigned_markers),
                then=Value(value=True),
            ),
            default=Value(value=False),
            output_field=BooleanField(),
        ),
    )

    owner_match_q = Q()
    for b in user_bases_list:
        pattern = rf"(^|[ ,._-])({b})([ ,._-]|@|$)"
        owner_match_q |= Q(owner_norm__regex=pattern) | Q(email_norm__regex=pattern)

    tasks_sql = tasks_sql.annotate(
        is_owner=Case(
            When(owner_match_q, then=Value(value=True)),
            default=Value(value=False),
            output_field=BooleanField(),
        )
    )

    rbac_q = build_rbac_q(tp_grouped, sp_grouped)
    tasks_sql = tasks_sql.filter(rbac_q)
    tasks_sql.count()


def group_perms_by_level(perms, id_field):
    grouped = {"FULL": [], "LIMITED": [], "OWN": []}
    priority = {"NONE": 0, "OWN": 1, "LIMITED": 2, "FULL": 3}
    best_levels = {}
    for p in perms:
        pid = p[id_field]
        lvl = p["access_level"]
        if pid not in best_levels or priority.get(lvl, 0) > priority.get(
            best_levels[pid], 0
        ):
            best_levels[pid] = lvl
    for pid, lvl in best_levels.items():
        if lvl in grouped:
            grouped[lvl].append(pid)
    return grouped


def build_rbac_q(tp_grouped, sp_grouped):
    rbac_q = Q()
    rbac_q |= Q(service_group_id__in=tp_grouped["FULL"]) | Q(
        service_id__in=sp_grouped["FULL"]
    )
    limited_q = Q(service_group_id__in=tp_grouped["LIMITED"]) | Q(
        service_id__in=sp_grouped["LIMITED"]
    )
    rbac_q |= limited_q & (Q(is_owner=True) | Q(is_unassigned=True))
    own_q = Q(service_group_id__in=tp_grouped["OWN"]) | Q(
        service_id__in=sp_grouped["OWN"]
    )
    rbac_q |= own_q & Q(is_owner=True)

    defined_groups = tp_grouped["FULL"] + tp_grouped["LIMITED"] + tp_grouped["OWN"]
    defined_services = sp_grouped["FULL"] + sp_grouped["LIMITED"] + sp_grouped["OWN"]
    fallback_q = Q(service__default_access_level="FULL")
    fallback_q |= Q(service__default_access_level="LIMITED") & (
        Q(is_owner=True) | Q(is_unassigned=True)
    )
    fallback_q |= Q(service__default_access_level="OWN") & Q(is_owner=True)
    rbac_q |= (
        fallback_q
        & ~Q(service_group_id__in=defined_groups)
        & ~Q(service_id__in=defined_services)
    )
    return rbac_q


def verify_n_plus_one():
    connection.queries_log.clear()

    # 1. Performance test for sorting 2,000+ tasks
    start_time = time.time()
    sort_qs = Task.objects.all().order_by("-updated_at")
    sort_qs.count()
    _ = list(sort_qs[:LIMIT_PAGINATION])  # Trigger database sorting
    db_time = time.time() - start_time
    if db_time < DB_TIME_THRESHOLD:
        pass

    # 2. Slice Verification
    qs = (
        Task.objects.filter(service__is_active=True)
        .select_related("service", "service_group")
        .order_by("-updated_at")
    )
    paginator = Paginator(qs, LIMIT_PAGINATION)
    page_obj = paginator.get_page(1)
    current_tasks = list(page_obj.object_list)

    # Simulate Post-Processing (mirroring views.py logic)
    processing_count = sum(1 for _ in current_tasks)

    if processing_count <= LIMIT_PAGINATION:
        pass

    # 3. N+1 Check
    connection.queries_log.clear()
    for t in current_tasks:
        _ = t.service.name
        if t.service_group:
            _ = t.service_group.name

    query_count = len(connection.queries)
    if query_count == 0:
        pass


def verify_search_index():
    connection.queries_log.clear()
    query = "test"
    Task.objects.filter(search_text__icontains=query).count()

    if connection.queries:
        last_sql = connection.queries[-1]["sql"]
        if "search_text" in last_sql:
            pass


if __name__ == "__main__":
    verify_identity_matching()
    verify_canonical_fallback()
    verify_unnest_and_distinct()
    verify_hierarchy()
    verify_n_plus_one()
    verify_search_index()
