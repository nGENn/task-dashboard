from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0034_clean_sso_groups"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="task",
            index=GinIndex(
                fields=["search_text"],
                name="task_search_text_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
