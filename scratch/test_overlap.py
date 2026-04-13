import django
from django.db import connection
from task_dashboard.users.models import Task
import json

messy_str = 'd.delta@example.com, h.hotel@example.com, delta@example.com, c.gamma@example.com, b.beta@example.com, e.epsilon@example.com, f.zeta@example.com, a.alpha@example.com, g.eta@example.com, d.delta@example.com, h.hotel@example.com, delta@example.com, c.gamma@example.'

# First let's guarantee this string is in a task for testing
t, created = Task.objects.get_or_create(
    external_id='test-bag-1',
    service_id=Task.objects.first().service_id if Task.objects.exists() else 0,
    defaults={'title': 'Bag of words test', 'owner': messy_str, 'status': 'open', 'customer': 'Test', 'priority': 'normal'}
)
if not created and t.owner != messy_str:
    t.owner = messy_str
    t.save()

search_term = 'alpha@example.com'
print(f"Testing regexp_split_to_array and && against: '{search_term}'")

# How users/views.py currently does it:
# (ARRAY(SELECT trim(unnest(string_to_array(owner, ',')))) && %s::text[])

# Let's test regexp_split_to_array
# We want to split on commas AND spaces, removing any stray non-alphanumeric at the end if needed,
# or simply we split by commas and spaces.
# Actually, the user asks to "Prove that you can extract alpha from a string of 10 owners using the PostgreSQL && (Overlap) operator."
query1 = """
SELECT id, owner FROM users_task
WHERE regexp_split_to_array(owner, '\\s*[,;\\s]+\\s*') && ARRAY[%s]::text[]
"""

# Try testing finding 'alpha@example.com'
with connection.cursor() as cursor:
    cursor.execute(query1, [search_term])
    rows = cursor.fetchall()
    print(f"Found {len(rows)} using regexp_split_to_array \\s*[,;\\s]+\\s* for exact {search_term}")

# What if we search for "alpha"? 
# PostgreSQL overlap `&&` only matches whole array elements. For partial matches,
# unnesting is required. However, the user said "extract alpha from a string of 10 owners using the PostgreSQL && (Overlap) operator."
# Wait, user said "Prove that you can extract alpha from a string of 10 owners". This likely means `alpha@example.com` will be partially broken down, or they want us to search for `alpha@example.com` specifically, OR maybe just `alpha`? Let's test both.

query2 = """
SELECT id, owner FROM users_task
WHERE regexp_split_to_array(owner, '[^a-zA-Z0-9@.-]+') && ARRAY[%s]::text[]
"""

with connection.cursor() as cursor:
    cursor.execute(query2, ['alpha@example.com'])
    rows2 = cursor.fetchall()
    print(f"Found {len(rows2)} using split non-word for 'alpha@example.com'")

    cursor.execute(query2, ['alpha'])
    rows3 = cursor.fetchall()
    print(f"Found {len(rows3)} using split non-word for 'alpha' directly")

# What if we extract the local-part using regex as well
query3 = """
SELECT id, owner FROM users_task
WHERE EXISTS (
    SELECT 1 FROM unnest(regexp_split_to_array(owner, '[^a-zA-Z0-9@.-]+')) AS elem
    WHERE elem ILIKE '%%alpha%%'
)
"""
with connection.cursor() as cursor:
    cursor.execute(query3)
    rows4 = cursor.fetchall()
    print(f"Found {len(rows4)} using ILIKE '%%alpha%%'")
