from django.db.models import CharField
from django.db.models import Func
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.db.models.functions import Lower
from django.db.models.functions import Replace
from django.db.models.functions import Trim


class Unaccent(Func):
    function = "UNACCENT"


class SplitPart(Func):
    function = "SPLIT_PART"


class RegexpReplace(Func):
    function = "REGEXP_REPLACE"


def db_norm(expr):
    """Normalize a DB expression: lowercase, trim, replace German umlauts, unaccent."""
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
    """Normalize a DB expression to alphanumeric prefix."""
    prefix = SplitPart(db_norm(expr), Value(value="@"), 1, output_field=CharField())
    return RegexpReplace(
        prefix,
        Value(value=r"[^a-z0-9]"),
        Value(value=""),
        flags="g",
        output_field=CharField(),
    )
