from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0031_add_owner_gin_indexes'),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS idx_task_owner_array;",
            reverse_sql=""
        ),
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS idx_task_owner_email_array;",
            reverse_sql=""
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX idx_task_owner_array ON users_task USING GIN (
                    regexp_split_to_array(
                        unaccent(
                            replace(replace(replace(lower(owner), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')
                        ),
                        '[^a-z0-9@.-]+'
                    )
                );
            """,
            reverse_sql="DROP INDEX IF EXISTS idx_task_owner_array;"
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX idx_task_owner_email_array ON users_task USING GIN (
                    regexp_split_to_array(
                        unaccent(
                            replace(replace(replace(lower(owner_email), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')
                        ),
                        '[^a-z0-9@.-]+'
                    )
                );
            """,
            reverse_sql="DROP INDEX IF EXISTS idx_task_owner_email_array;"
        ),
    ]
