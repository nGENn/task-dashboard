from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0035_search_text_trigram_index"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="task",
            index=models.Index(fields=["updated_at"], name="task_updated_at_idx"),
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(fields=["created_at"], name="task_created_at_idx"),
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(fields=["due_date"], name="task_due_date_idx"),
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(fields=["status"], name="task_status_idx"),
        ),
    ]
