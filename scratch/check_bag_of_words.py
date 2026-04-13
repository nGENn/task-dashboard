from django.db import connection

from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task

service = ServiceConfiguration.objects.first()
service_id = service.id if service else None

if service_id is None:
    pass
else:
    test_str = (
        "d.delta@example.com, h.hotel@example.com, delta@example.com, "
        "c.gamma@example.com, b.beta@example.com, e.epsilon@example.com, "
        "f.zeta@example.com, a.alpha@example.com, g.eta@example.com, "
        "Alice i.india"
    )

    Task.objects.update_or_create(
        external_id="bag-of-words-test",
        defaults={
            "title": "Test",
            "owner": test_str,
            "service_id": service_id,
            "status": "open",
        },
    )

    query = """
    SELECT id, owner FROM users_task
    WHERE regexp_split_to_array(owner, '[^a-zA-Z0-9@.-]+') && ARRAY[%s, %s]::text[]
      AND external_id = 'bag-of-words-test'
    """

    with connection.cursor() as cursor:
        # 1. Try extracting alpha
        cursor.execute(query, ["alpha", "alpha@example.com"])

        # 2. Try extracting Alice from Alice i.india
        cursor.execute(query, ["alice", "Alice"])

        # 3. Try gamma with a dot
        Task.objects.update_or_create(
            external_id="bag-of-words-test-2",
            defaults={
                "title": "Test",
                "owner": "gamma@example.",
                "service_id": service_id,
                "status": "open",
            },
        )
        cursor.execute(
            """
        SELECT id, owner FROM users_task
        WHERE regexp_split_to_array(owner, '[^a-zA-Z0-9@.-]+') && ARRAY[%s]::text[]
          AND external_id = 'bag-of-words-test-2'
        """,
            ["gamma@example."],
        )
