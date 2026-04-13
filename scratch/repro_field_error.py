import os
import django
import sys

os.environ["DJANGO_READ_DOT_ENV_FILE"] = "True"
sys.path.append(os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from django.db.models import F, Value, CharField, Case, When, Q
from django.db.models.functions import Coalesce, Lower, Replace
from task_dashboard.users.models import Task

# Mock logic from views.py
unassigned_markers = ["", "-", "None", "Unassigned"]

class Unaccent(django.db.models.Func):
    function = "unaccent"

def get_base_expr(field_name):
    expr = Unaccent(Lower(F(field_name)))
    for old, new in [("oe", "o"), ("ae", "a"), ("ue", "u")]:
         expr = Replace(expr, Value(old), Value(new))
    return expr

try:
    base_tasks = Task.objects.annotate(
        canonical_owner=Coalesce(
            Case(When(~Q(owner_email__in=unassigned_markers), then=get_base_expr("owner_email")), default=None),
            Case(When(~Q(owner__in=unassigned_markers), then=get_base_expr("owner")), default=None),
            Value(""),
            output_field=CharField()
        )
    )

    print("Attempting distinct on canonical_owner with values()...")
    qs = (
        base_tasks.values("canonical_owner", "owner")
        .order_by("canonical_owner", "-owner")
        .distinct("canonical_owner")
    )
    print(f"First result: {list(qs[:1])}")

except Exception as e:
    import traceback
    traceback.print_exc()
