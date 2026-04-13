import re

def resolve_anchor(fragments, users_map=None):
    if users_map is None: users_map = {}
    
    def r_score(frag):
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
            # Maybe prefer longer full names? The prompt didn't specify beyond "Full Name"
            # But usually we don't want to sort by shortest if it's a name, maybe longest?
            # Prompt says: "Official User Email > Shortest Valid Email > Full Name"
            return (3, -len(fl), fl)
            
        # 4. Fallback (fragments like 'iota', 'alpha')
        # We can rank these last and just use short length
        return (4, len(fl), fl)

    if not fragments:
        return None

    # First clean and normalize list
    cleaned = []
    for f in fragments:
        f = f.strip()
        if not f: continue
        f = re.sub(r"@example\.$", "@example.com", f) if f.endswith("@example.") else f
        cleaned.append(f)
        
    if not cleaned: return None
        
    best = min(cleaned, key=r_score)
    return best

print("Testing resolve_anchor:")
fragments1 = ["iota", "Charlie Iota", "c.iota", "iota@example.com"]
print(f"List 1: {fragments1} => Anchor: {resolve_anchor(fragments1)}")

fragments2 = ["alpha", "Alice Alpha", "alpha@example."]
print(f"List 2: {fragments2} => Anchor: {resolve_anchor(fragments2)}")

fragments3 = ["delta", "David Delta", "delta@example.com", "d.delta@example.com"]
print(f"List 3: {fragments3} => Anchor: {resolve_anchor(fragments3)}")

fragments4 = ["Zeta Bob", "zeta@example.com", "Bob Zeta"]
print(f"List 4: {fragments4} => Anchor: {resolve_anchor(fragments4)}")
