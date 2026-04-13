from django.db import connection

query = """
SELECT regexp_split_to_array(
    'Bob Zeta, delta@example.com, Charlie alpha', '\\s*[,; \n]+\\s*'
)
"""
with connection.cursor() as cursor:
    cursor.execute(query)
