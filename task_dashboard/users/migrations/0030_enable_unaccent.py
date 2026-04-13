from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0029_migrate_task_groups'),
    ]

    operations = [
        UnaccentExtension(),
    ]
