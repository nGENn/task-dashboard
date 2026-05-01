from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0036_task_field_indexes'),
    ]

    operations = [
        # Drop the SSOGroup marker table — group tracking is now per-user via
        # User.sso_synced_groups, so the global marker model is no longer needed.
        migrations.DeleteModel(
            name='SSOGroup',
        ),
        # Remove the toggle that allowed disabling group sync. Sync is now always
        # active; when no groups arrive from the token a fallback group is used.
        migrations.RemoveField(
            model_name='globalsetting',
            name='sso_group_sync',
        ),
        # Per-user record of which group names were last assigned by SSO sync.
        # This lets _sync_groups remove only SSO-assigned groups on re-login
        # without touching manually assigned groups.
        migrations.AddField(
            model_name='user',
            name='sso_synced_groups',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
