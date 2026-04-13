import os
import sys
from pathlib import Path

import django

os.environ["DJANGO_READ_DOT_ENV_FILE"] = "True"
sys.path.append(str(Path.cwd()))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from django.db.models import Case  # noqa: E402
from django.db.models import CharField  # noqa: E402
from django.db.models import F  # noqa: E402
from django.db.models import Q  # noqa: E402
from django.db.models import Value  # noqa: E402
from django.db.models import When  # noqa: E402
from django.db.models.functions import Coalesce  # noqa: E402
from django.db.models.functions import Lower  # noqa: E402
from django.db.models.functions import Replace  # noqa: E402

from task_dashboard.users.models import Task  # noqa: E402

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
            Case(
                When(
                    ~Q(owner_email__in=unassigned_markers),
                    then=get_base_expr("owner_email"),
                ),
                default=None,
            ),
            Case(
                When(~Q(owner__in=unassigned_markers), then=get_base_expr("owner")),
                default=None,
            ),
            Value(""),
            output_field=CharField(),
        )
    )

    qs = (
        base_tasks.values("canonical_owner", "owner")
        .order_by("canonical_owner", "-owner")
        .distinct("canonical_owner")
    )

except Exception:  # noqa: BLE001
    import traceback

    traceback.print_exc()
