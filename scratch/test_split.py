import django
from django.db import connection

query = """
SELECT regexp_split_to_array('Alice, Bob; Charlie alpha, delta@example.', '\\s*[,;]+\\s*')
"""
with connection.cursor() as cursor:
    cursor.execute(query)
    print(cursor.fetchall()[0][0])
