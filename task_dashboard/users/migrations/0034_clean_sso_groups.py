from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0033_task_original_priority_task_original_status"),
    ]

    operations = [
        migrations.DeleteModel(
            name="SSOGroup",
        ),
        migrations.AddField(
            model_name="globalsetting",
            name="sso_default_group",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Fallback group assigned to SSO users when Keycloak provides no groups. "
                    "Leave blank to use the built-in 'sso-default-fallback' group."
                ),
                max_length=150,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="sso_synced_groups",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
