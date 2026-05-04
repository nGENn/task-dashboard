import re
import unicodedata
from typing import Any

from django.utils.translation import gettext_lazy as _

from task_dashboard.users.models import User

MIN_TOKEN_LENGTH = 3


def _extract_email(s: str) -> str:
    """Extract email from 'Name <email@domain.com>' or return string as-is."""
    if "<" in s and ">" in s:
        return s.split("<")[-1].split(">")[0].strip()
    return s.strip()


def _is_valid_email(s: str) -> bool:
    """True if s is a syntactically complete email (non-empty, non-trailing-dot TLD)."""
    email = _extract_email(s)
    if "@" not in email:
        return False
    domain = email.split("@")[-1]
    return "." in domain and not domain.endswith(".")


BRIDGE_THRESHOLD = 4
BRIDGE_CORE_LENGTH = 5
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


def normalize_identity_string(s: str) -> str:
    if not s:
        return ""
    s_norm = (
        str(s).lower().strip().replace("ö", "oe").replace("ä", "ae").replace("ü", "ue")
    )
    return (
        unicodedata.normalize("NFKD", s_norm).encode("ASCII", "ignore").decode("utf-8")
    )


def get_user_tokens(user_or_str) -> list[str]:
    """Extract normalized search tokens from a user object or string."""
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
        if tk and len(tk) >= MIN_TOKEN_LENGTH and tk.lower() not in UNASSIGNED_MARKERS
    ]


def identity_score(users_map: dict, frag: str) -> tuple:
    fl = frag.lower().strip()
    if fl in users_map and getattr(users_map[fl], "email", None):
        return (1, len(users_map[fl].email), users_map[fl].email)
    if "@" in fl and "." in fl.split("@")[1] and not fl.endswith("."):
        return (2, len(frag), frag)
    if " " in frag:
        return (3, -len(frag), frag)
    return (4, len(frag), frag)


def find_anchor_match(merged: dict, anchor: str) -> str | None:
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


def _complete_truncated_email(label: str, users_map: dict) -> str:
    """Return a completed email when label ends with '@domain.', else return label."""
    if "@" not in label or not label.endswith("."):
        return label
    prefix = label.lower().rstrip(".")
    for user_email, user_obj in users_map.items():
        if user_email.startswith(prefix + "."):
            return user_obj.email
    return re.sub(r"@example\.$", "@example.com", label)


def _has_domain_conflict(labels: set, new_email: str) -> bool:
    """Return True if any existing valid email in labels has a different domain."""
    new_domain = new_email.lower().split("@")[-1]
    return any(
        _is_valid_email(existing) and existing.lower().split("@")[-1] != new_domain
        for existing in labels
    )


def add_to_merged(  # noqa: C901
    merged: dict[str, dict[str, Any]],
    users_map: dict,
    label: str,
    has_task: bool,  # noqa: FBT001
) -> None:
    label = label.strip()
    cleaned_label = _complete_truncated_email(label, users_map)

    def py_norm(s):
        prefix = normalize_identity_string(s).split("@")[0]
        return re.sub(r"[^a-z0-9]", "", prefix)

    anchor = py_norm(cleaned_label)
    if not anchor:
        return

    match = find_anchor_match(merged, anchor)

    if match and _is_valid_email(cleaned_label):
        if _has_domain_conflict(merged[match].get("labels", set()), cleaned_label):
            match = None
            email_part = _extract_email(cleaned_label)
            anchor = re.sub(r"[^a-z0-9]", "", normalize_identity_string(email_part))

    if match:
        g = merged[match]
        g["labels"].update({cleaned_label, label})
        if has_task:
            g["has_tasks"] = True
        if len(anchor) < len(match):
            merged[anchor] = merged.pop(match)
            match = anchor

        best_cand_score = identity_score(users_map, cleaned_label)
        if best_cand_score < identity_score(users_map, g["best"]):
            g["best"] = best_cand_score[2]
    elif anchor in merged:
        g = merged[anchor]
        g["labels"].update({cleaned_label, label})
        if has_task:
            g["has_tasks"] = True
        best_cand_score = identity_score(users_map, cleaned_label)
        if best_cand_score < identity_score(users_map, g["best"]):
            g["best"] = best_cand_score[2]
    else:
        merged[anchor] = {
            "best": identity_score(users_map, cleaned_label)[2],
            "labels": {cleaned_label, label},
            "has_tasks": has_task,
        }


def build_token_index(merged: dict[str, dict[str, Any]]) -> dict[str, str]:
    token_to_canonical: dict[str, str] = {}
    for anchor, g in merged.items():
        best = g["best"]
        for label in g["labels"]:
            l_norm = normalize_identity_string(label)
            label_tokens = [
                lt
                for lt in re.split(r"[^a-z0-9@.-]+", l_norm)
                if lt and len(lt) >= MIN_TOKEN_LENGTH and lt not in UNASSIGNED_MARKERS
            ]
            for lt in label_tokens:
                token_to_canonical.setdefault(lt, best)
        if anchor and len(anchor) >= MIN_TOKEN_LENGTH:
            token_to_canonical.setdefault(anchor, best)
    return token_to_canonical


def get_identity_bridging_data(
    base_tasks, user
) -> tuple[dict, dict[str, set[str]], dict[str, str]]:
    """Build merged identity dict, reverse mapping, and token index."""
    users_map: dict = {}
    for u in User.objects.all():
        if u.email:
            users_map[u.email.lower()] = u
        if u.name:
            users_map[u.name.lower()] = u

    owner_pool = list(base_tasks.order_by().values("owner", "owner_email").distinct())
    pool: list[str] = []
    for p in owner_pool:
        raw_labels = []
        if p["owner"]:
            raw_labels.extend([x.strip() for x in p["owner"].split(",")])
        if p["owner_email"]:
            raw_labels.extend([x.strip() for x in p["owner_email"].split(",")])
        pool.extend(v for v in raw_labels if v and v.lower() not in UNASSIGNED_MARKERS)

    merged: dict[str, dict[str, Any]] = {}
    user_raw = [getattr(user, "email", ""), getattr(user, "name", "")]
    for r in user_raw:
        add_to_merged(merged, users_map, r, has_task=False)
    for label in pool:
        add_to_merged(merged, users_map, label, has_task=True)

    best_to_raw: dict[str, set[str]] = {}
    for g in merged.values():
        best_to_raw.setdefault(g["best"], set()).update(g["labels"])

    token_to_canonical = build_token_index(merged)
    return merged, best_to_raw, token_to_canonical


def post_process_task_owners(task, token_to_canonical: dict, merged: dict) -> None:  # noqa: C901, PLR0912
    """Map raw owner/email strings to canonical display names on task."""
    raw_owners = []
    if task.owner:
        raw_owners.extend([o.strip() for o in task.owner.split(",")])
    if task.owner_email:
        raw_owners.extend([o.strip() for o in task.owner_email.split(",")])

    # Phase 1: Collect all identities found via exact email tokens across ALL
    # raw_owners. We also collect all tokens associated with these identities.
    identities_with_email: dict[str, set[str]] = {}
    for o in raw_owners:
        if not o or o.lower() in UNASSIGNED_MARKERS:
            continue
        o_norm = normalize_identity_string(o)
        tokens = [tk for tk in re.split(r"[^a-z0-9@.-]+", o_norm) if tk]
        for tk in tokens:
            if "@" in tk and tk in token_to_canonical:
                canon = token_to_canonical[tk]
                if canon not in identities_with_email:
                    # Get all tokens for this identity from merged data
                    all_tokens = set()
                    for g in merged.values():
                        if g["best"] == canon:
                            for label in g["labels"]:
                                tks = get_user_tokens(label)
                                all_tokens.update(tks)
                                # Locally include email prefixes to help bridging
                                for t in tks:
                                    if "@" in t:
                                        prefix = t.split("@")[0]
                                        if len(prefix) >= MIN_TOKEN_LENGTH:
                                            all_tokens.add(prefix)
                    identities_with_email[canon] = all_tokens

    canonical_names = set(identities_with_email.keys())
    for o in raw_owners:
        if not o or o.lower() in UNASSIGNED_MARKERS:
            continue
        o_norm = normalize_identity_string(o)
        tokens = [tk for tk in re.split(r"[^a-z0-9@.-]+", o_norm) if tk]

        # If this owner string contains an email token we already matched, skip it.
        if any(
            "@" in tk and token_to_canonical.get(tk) in identities_with_email
            for tk in tokens
        ):
            continue

        # Try to find a match via tokens
        found = False
        for tk in tokens:
            # 1. Prefer identities already found via email on this task if
            #    they share this token
            better_match = None
            for email_canon, email_tokens in identities_with_email.items():
                if tk in email_tokens:
                    better_match = email_canon
                    break

            if better_match:
                canonical_names.add(better_match)
                found = True
                break

            # 2. Global fallback via token index
            if tk in token_to_canonical:
                canonical_names.add(token_to_canonical[tk])
                found = True
                break

        if not found:
            canonical_names.add(o)

    task.display_owner_list = sorted(canonical_names)


def get_unassigned_label() -> str:
    return str(_("Unassigned"))
