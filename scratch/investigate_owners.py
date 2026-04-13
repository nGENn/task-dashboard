# We are in manage.py shell, so django is already set up.
from task_dashboard.users.models import Task

messy_owners = Task.objects.exclude(owner__isnull=True).values_list(
    "owner", "owner_email"
)

all_messy = set()
for o, e in messy_owners:
    if o:
        all_messy.add(o)
    if e:
        all_messy.add(e)

results = []
for m in all_messy:
    score = m.count(",") * 5 + m.count(" ") + (20 if m.strip().endswith(".") else 0)
    if score > 0:
        results.append((score, m))

results.sort(reverse=True, key=lambda x: x[0])
for _s, _m in results[:10]:
    pass
