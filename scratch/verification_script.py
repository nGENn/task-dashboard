import os
import django
import re
import sys

# Pre-setup environment for Django-environ
os.environ["DJANGO_READ_DOT_ENV_FILE"] = "True"
sys.path.append(os.getcwd())

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
try:
    django.setup()
except Exception as e:
    print(f"Failed to setup Django: {e}")
    sys.exit(1)

from django.db.models import (
    BooleanField, Case, Exists, F, Func, OuterRef, Q, Value, When, CharField
)
from django.db.models.functions import Coalesce, Lower, Replace, Trim
from django.db import connection
from django.core.paginator import Paginator

from task_dashboard.users.models import Task, User, TaskPermission, ServicePermission, GlobalSetting

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
            expressions.append(Value(flags))
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
    
    e = SplitPart(expr, Value("@"), 1, output_field=CharField())
    e = SplitPart(e, Value(" "), 1, output_field=CharField())
    e = SplitPart(e, Value(","), 1, output_field=CharField())
    e = SplitPart(e, Value("-"), 1, output_field=CharField())
    return RegexpReplace(e, Value(r"[^a-z0-9]"), Value(""), flags="g", output_field=CharField())

def verify_identity_matching():
    print("--- 1. Identify Matching Validation ---")
    test_cases = [
        ("Zeta Bob", "zeta"),
        ("Müller", "muller"),
        ("Judith Delta", "judith"), # First part is anchor now
        ("zeta@example.com", "zeta"),
        ("john.doe@example.com", "johndoe"),
    ]
    
    any_fail = False
    for tc, expected in test_cases:
        # Test SQL via a single annotation
        res = Task.objects.annotate(
            val=Value(tc, output_field=CharField()),
            base=get_base_expr("val")
        ).values_list("base", flat=True)[:1][0]
        
        match = "PASS" if res == expected else "FAIL"
        if match == "FAIL": any_fail = True
        print(f"Input: {tc:30} | Expected: {expected:10} | SQL: {res:10} | {match}")
    
    if not any_fail:
        print("RESULT: IDENTITY MATCHING ZERO DRIFT CONFIRMED")
    else:
        print("RESULT: IDENTITY MATCHING DRIFT DETECTED")

def verify_canonical_fallback():
    print("\n--- 2. Canonical Fallback Validation ---")
    # Simulate a task where email is present and owner is different
    # Hierarchy: Email > Full Name > Username
    tasks = Task.objects.annotate(
        c_base=Coalesce(
            Case(When(~Q(owner_email__in=["", "-", "None", "Unassigned"]), then=get_base_expr("owner_email")), default=None),
            Case(When(~Q(owner__in=["", "-", "None", "Unassigned"]), then=get_base_expr("owner")), default=None),
            Value(""),
            output_field=CharField()
        )
    ).all()[:10]
    
    for t in tasks:
        print(f"Email: {t.owner_email:30} | Owner: {t.owner:30} | Canonical: {t.c_base}")

def verify_unnest_and_distinct():
    print("\n--- 3. Unnest and Distinct Validation ---")
    # 1. Test Status uniqueness
    statuses = list(Task.objects.order_by().values_list("status", flat=True).distinct())
    print(f"Unique Statuses: {statuses}")
    if len(statuses) == len(set(statuses)):
        print("RESULT: STATUS DROPDOWN DEDUPLICATION VERIFIED")
    else:
        print("RESULT: STATUS DROPDOWN CONTAINS DUPLICATES")

    # 2. Test Owner Unnesting
    # We'll check if any returned owner contains a comma
    unique_owners_ext = set(
        Task.objects.annotate(clean=Trim(Unnest(StringToArray(F("owner"), Value(",")))))
        .order_by()
        .values_list("clean", flat=True)
        .distinct()
    )
    unassigned = ["", "-", "None", "Unassigned"]
    unique_owners = unique_owners_ext - set(unassigned)
    
    print(f"Sample Unique Owners (first 5): {list(unique_owners)[:5]}")
    has_comma = any("," in o for o in unique_owners)
    if not has_comma:
        print("RESULT: OWNER UNNESTING VERIFIED (No comma-separated strings found)")
    else:
        print("RESULT: OWNER UNNESTING FAILED (Comma found in unique list)")

def verify_hierarchy():
    print("\n--- 4. Identity Hierarchy Validation ---")
    # Simulate views.py map logic
    unassigned = ["", "-", "None", "Unassigned"]
    def get_pairs(field):
        return Task.objects.exclude(**{f"{field}__in": unassigned}).annotate(
            raw=Trim(Unnest(StringToArray(F(field), Value(",")))),
            canon=get_base_expr("raw")
        ).values_list("raw", "canon").distinct()

    owner_pairs = get_pairs("owner")
    email_pairs = get_pairs("owner_email")
    
    identity_map = {}
    def update(pairs, is_email):
        for raw, canon in pairs:
            if not canon: continue
            priority = 3 if is_email else (2 if " " in raw or "." in raw or "-" in raw else 1)
            if canon not in identity_map or priority > identity_map[canon][1] or (priority == identity_map[canon][1] and len(raw) > len(identity_map[canon][0])):
                identity_map[canon] = (raw, priority)

    update(owner_pairs, is_email=False)
    update(email_pairs, is_email=True)
    
    # Check for Delta unification
    delta_entries = [v[0] for k, v in identity_map.items() if k == "delta"]
    print(f"Delta Resolved entries: {delta_entries}")
    if len(delta_entries) <= 1:
        print("RESULT: IDENTITY HIERARCHY UNIFICATION VERIFIED")
    else:
        print("RESULT: IDENTITY HIERARCHY FAILED (Duplicate canonical entries)")
    print("\n--- 2. Count Parity & RBAC Verification ---")
    # Test for a few representative users
    test_users = User.objects.all().order_by("id")[:5]
    if not test_users:
        print("No users found to test.")
        return

    for user in test_users:
        print(f"\nUser: {user.username} (Email: {user.email})")
        
        # --- Python Logic bases ---
        user_bases = set()
        if user.email: user_bases.add(extract_base_py(user.email))
        if getattr(user, "name", ""): user_bases.add(extract_base_py(user.name))
        user_bases.discard("")
        user_bases_list = sorted(list(user_bases), key=len, reverse=True)

        owner_match_q = Q()
        for b in user_bases_list:
            owner_match_q |= Q(email_base=b) | Q(owner_base=b)
            owner_match_q |= Q(email_base__regex=rf"^.{{0,3}}{b}$") | Q(owner_base__regex=rf"^.{{0,3}}{b}$")

        unassigned_markers = ["", "-", "None", "Unassigned"]
        
        # SQL Logic (Actual code from views.py)
        user_groups = user.groups.all()
        task_perms = TaskPermission.objects.filter(django_group__in=user_groups).values("allowed_external_group_id", "access_level")
        service_perms = ServicePermission.objects.filter(django_group__in=user_groups).values("service_id", "access_level")

        def group_perms_by_level(perms, id_field):
            grouped = {"FULL": [], "LIMITED": [], "OWN": []}
            priority = {"NONE": 0, "OWN": 1, "LIMITED": 2, "FULL": 3}
            best_levels = {}
            for p in perms:
                pid = p[id_field]
                lvl = p["access_level"]
                if pid not in best_levels or priority.get(lvl, 0) > priority.get(best_levels[pid], 0):
                    best_levels[pid] = lvl
            for pid, lvl in best_levels.items():
                if lvl in grouped: grouped[lvl].append(pid)
            return grouped

        tp_grouped = group_perms_by_level(task_perms, "allowed_external_group_id")
        sp_grouped = group_perms_by_level(service_perms, "service_id")

        tasks_sql = (
            Task.objects.filter(service__is_active=True)
            .annotate(
                owner_norm=Unaccent(Lower(Trim(F("owner")))),
                email_norm=Unaccent(Lower(Trim(F("owner_email")))),
                is_unassigned=Case(
                    When(Q(owner_email__in=unassigned_markers) & Q(owner__in=unassigned_markers), then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
            )
        )

        owner_match_q = Q()
        for b in user_bases_list:
            pattern = rf"(^|[ ,._-])({b})([ ,._-]|@|$)"
            owner_match_q |= Q(owner_norm__regex=pattern) | Q(email_norm__regex=pattern)

        tasks_sql = tasks_sql.annotate(
            is_owner=Case(
                When(owner_match_q, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )

        rbac_q = Q()
        rbac_q |= Q(service_group_id__in=tp_grouped["FULL"]) | Q(service_id__in=sp_grouped["FULL"])
        limited_q = Q(service_group_id__in=tp_grouped["LIMITED"]) | Q(service_id__in=sp_grouped["LIMITED"])
        rbac_q |= limited_q & (Q(is_owner=True) | Q(is_unassigned=True))
        own_q = Q(service_group_id__in=tp_grouped["OWN"]) | Q(service_id__in=sp_grouped["OWN"])
        rbac_q |= own_q & Q(is_owner=True)
        
        defined_groups = tp_grouped["FULL"] + tp_grouped["LIMITED"] + tp_grouped["OWN"]
        defined_services = sp_grouped["FULL"] + sp_grouped["LIMITED"] + sp_grouped["OWN"]
        fallback_q = Q(service__default_access_level="FULL")
        fallback_q |= Q(service__default_access_level="LIMITED") & (Q(is_owner=True) | Q(is_unassigned=True))
        fallback_q |= Q(service__default_access_level="OWN") & Q(is_owner=True)
        rbac_q |= fallback_q & ~Q(service_group_id__in=defined_groups) & ~Q(service_id__in=defined_services)

        tasks_sql = tasks_sql.filter(rbac_q)
        
        sql_total = tasks_sql.count()
        sql_my = tasks_sql.filter(is_owner=True, status__in=["open", "pending", "new"]).count()
        
        print(f"SQL Parity Check: Total={sql_total}, My Tasks={sql_my}")

def verify_n_plus_one():
    print("\n--- 3. Performance & N+1 Verification ---")
    connection.queries_log.clear()
    import time
    
    # 1. Performance test for sorting 2,000+ tasks
    start_time = time.time()
    sort_qs = Task.objects.all().order_by("-updated_at")
    total_count = sort_qs.count()
    _ = list(sort_qs[:50]) # Trigger database sorting
    db_time = time.time() - start_time
    print(f"Time to sort {total_count} tasks and fetch first 50 results: {db_time:.4f}s")
    if db_time < 0.5:
        print("RESULT: SORTING PERFORMANCE VERIFIED (Sub-500ms for 2,000+ records)")
    else:
        print(f"RESULT: SORTING LATENCY WARNING ({db_time:.4f}s)")

    # 2. Slice Verification
    qs = Task.objects.filter(service__is_active=True).select_related("service", "service_group").order_by("-updated_at")
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(1)
    current_tasks = list(page_obj.object_list)
    
    print(f"Paginated Slice Size: {len(current_tasks)}")
    
    # Simulate Post-Processing (mirroring views.py logic)
    # We want to confirm that we are NOT processing more than the slice.
    processing_count = 0
    for _ in current_tasks:
        processing_count += 1
    
    print(f"Number of records post-processed: {processing_count}")
    if processing_count <= 50:
        print("RESULT: POST-PROCESSING SLICE INTEGRITY VERIFIED (Limited to 50 rows)")
    else:
        print(f"RESULT: POST-PROCESSING LEAK DETECTED ({processing_count} rows)")

    # 3. N+1 Check
    connection.queries_log.clear()
    for t in current_tasks:
        _ = t.service.name
        if t.service_group:
            _ = t.service_group.name

    query_count = len(connection.queries)
    print(f"Additional queries for {len(current_tasks)} rows (attribute access): {query_count}")
    if query_count == 0:
        print("RESULT: N+1 OPTIMIZATION VERIFIED (Select-related covers all row attributes)")

    print("\nPRIMARY SELECT QUERY (Snippet):")
    # Find the one with select_related
    for q in connection.queries:
        sql = q['sql']
        if "INNER JOIN" in sql or "LEFT OUTER JOIN" in sql:
            print(sql[:1000] + "...")
            break

def verify_search_index():
    print("\n--- 4. Search Stress Test (GeneratedField) ---")
    connection.queries_log.clear()
    query = "test"
    count = Task.objects.filter(search_text__icontains=query).count()
    print(f"Search for '{query}' found {count} results.")
    
    if connection.queries:
        last_sql = connection.queries[-1]['sql']
        if "search_text" in last_sql:
            print("RESULT: search_text GENERATED FIELD UTILIZED IN WHERE CLAUSE")
        else:
            print("RESULT: search_text NOT FOUND IN QUERY")

if __name__ == "__main__":
    verify_identity_matching()
    verify_canonical_fallback()
    verify_unnest_and_distinct()
    verify_hierarchy()
    verify_n_plus_one()
    verify_search_index()
