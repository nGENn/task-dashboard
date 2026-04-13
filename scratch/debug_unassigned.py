import os

import django
from django.db.models import BooleanField
from django.db.models import Case
from django.db.models import F
from django.db.models import Func
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Coalesce
from django.db.models.functions import Lower
from django.db.models.functions import Trim

# Set settings to config.settings.test which has our TESTING=True flag
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
django.setup()

from task_dashboard.users.models import ServiceConfiguration  # noqa: E402
from task_dashboard.users.models import Task  # noqa: E402


class Unaccent(Func):
    function = "UNACCENT"


class Replace(Func):
    function = "REPLACE"


UNASSIGNED_MARKERS = ["", "-", "none", "unassigned", "0", "null", "unassigned person"]


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


service = ServiceConfiguration.objects.first()
if not service:
    service = ServiceConfiguration.objects.create(
        name="dummy", service_type="zammad", is_active=True
    )

# Create test tasks
Task.objects.all().delete()
Task.objects.create(
    external_id="Z1", title="Z1", service=service, owner="Other Person", owner_email=""
)
Task.objects.create(
    external_id="Z2",
    title="Z2",
    service=service,
    owner="",
    owner_email="test@example.com",
)
Task.objects.create(
    external_id="Z3", title="Z3", service=service, owner="", owner_email=""
)

qs = Task.objects.annotate(
    onorm=db_norm(F("owner")),
    enorm=db_norm(F("owner_email")),
).annotate(
    is_unassigned=Case(
        When(
            onorm__in=UNASSIGNED_MARKERS,
            enorm__in=UNASSIGNED_MARKERS,
            then=Value(value=True),
        ),
        default=Value(value=False),
        output_field=BooleanField(),
    )
)

for _t in qs:
    pass
