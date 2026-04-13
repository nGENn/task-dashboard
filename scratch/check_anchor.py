import re


def get_ranking_score(frag, users_map=None):
    if users_map is None:
        users_map = {}
    fl = frag.lower().strip()
    # Clean dot-truncated emails
    fl = re.sub(r"@example\.$", "@example.com", fl) if fl.endswith("@example.") else fl

    # 1. Official User Email (exists in users_map and is associated with a user)
    # Note: assuming users_map has email lowercases as keys
    if fl in users_map and "@" in fl:
        return (1, len(fl), fl)

    # 2. Shortest Valid Email
    is_valid_email = "@" in fl and "." in fl.split("@")[1]
    if is_valid_email:
        # We want the *shortest* email, so length is the secondary sort key
        return (2, len(fl), fl)

    # 3. Full Name (has a space, assuming First Last)
    if " " in fl:
        # Prompt says: "Official User Email > Shortest Valid Email > Full Name"
        return (3, -len(fl), fl)

    # 4. Fallback (fragments like 'iota', 'alpha')
    # We can rank these last and just use short length
    return (4, len(fl), fl)


def resolve_anchor(fragments, users_map=None):
    if not fragments:
        return None

    # First clean and normalize list
    cleaned = []
    for f_raw in fragments:
        f_clean = f_raw.strip()
        if not f_clean:
            continue
        if f_clean.endswith("@example."):
            f_clean = re.sub(r"@example\.$", "@example.com", f_clean)
        cleaned.append(f_clean)

    if not cleaned:
        return None

    return min(cleaned, key=lambda f: get_ranking_score(f, users_map))


fragments1 = ["iota", "Charlie Iota", "c.iota", "iota@example.com"]

fragments2 = ["alpha", "Alice Alpha", "alpha@example."]

fragments3 = ["delta", "David Delta", "delta@example.com", "d.delta@example.com"]

fragments4 = ["Zeta Bob", "zeta@example.com", "Bob Zeta"]
