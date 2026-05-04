from django.db.models import Q

from task_dashboard.users.identity import UNASSIGNED_MARKERS
from task_dashboard.users.identity import get_user_tokens
from task_dashboard.users.models import ServicePermission
from task_dashboard.users.models import TaskPermission

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


def q_for_lvl(score: int) -> Q:
    if score == RBAC_FULL:
        return Q(id__isnull=False)
    if score == RBAC_LIMITED:
        return Q(is_owner=True) | Q(is_unassigned=True)
    if score == RBAC_OWN:
        return Q(is_owner=True)
    return Q(pk__in=[])


def get_rbac_q(user) -> Q:  # noqa: C901
    """Build the RBAC Q-object for a user based on their group memberships."""
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

    # Build a token-aware q_for_lvl to avoid relying on annotated fields
    user_tokens = get_user_tokens(user)

    def local_q_for_lvl(score: int) -> Q:
        if score == RBAC_FULL:
            return Q(id__isnull=False)

        # is_unassigned check without annotations
        # A task is unassigned ONLY if BOTH owner and owner_email are markers
        owner_is_marker = Q(owner__isnull=True) | Q(owner__exact="")
        email_is_marker = Q(owner_email__isnull=True) | Q(owner_email__exact="")
        for m in UNASSIGNED_MARKERS:
            if m:
                owner_is_marker |= Q(owner__iexact=m)
                email_is_marker |= Q(owner_email__iexact=m)
        is_unassigned_q = owner_is_marker & email_is_marker

        # owner match using tokens
        owner_tokens_q = Q()
        for t in user_tokens:
            if "@" in t:
                owner_tokens_q |= Q(owner_email__iexact=t) | Q(owner__icontains=t)
            else:
                # If the token is just a name, only match it against the owner field IF:
                # 1. The owner field does not look like an email address (no '@' symbol)
                # 2. AND the task does not have a valid owner_email claiming it.
                #    If owner_email contains '@', it belongs to a specific email
                #    identity,
                #    so a generic name token shouldn't override that.
                owner_tokens_q |= (
                    Q(owner__icontains=t)
                    & ~Q(owner__contains="@")
                    & ~Q(owner_email__contains="@")
                )

        if score == RBAC_LIMITED:
            return owner_tokens_q | is_unassigned_q
        if score == RBAC_OWN:
            return owner_tokens_q
        return Q(pk__in=[])

    rbac_q = Q()
    for name, score in group_perms.items():
        rbac_q |= Q(group=name) & local_q_for_lvl(score)
    for gid, score in group_id_perms.items():
        rbac_q |= Q(service_group_id=gid) & local_q_for_lvl(score)

    handled_groups = Q(group__in=group_perms.keys()) | Q(
        service_group_id__in=group_id_perms.keys()
    )
    for sid, score in service_perms.items():
        rbac_q |= Q(service_id=sid) & ~handled_groups & local_q_for_lvl(score)

    handled_all = handled_groups | Q(service_id__in=service_perms.keys())
    for level in ["FULL", "LIMITED", "OWN"]:
        rbac_q |= (
            Q(service__default_access_level=level)
            & ~handled_all
            & local_q_for_lvl(RBAC_MAP[level])
        )
    return rbac_q
