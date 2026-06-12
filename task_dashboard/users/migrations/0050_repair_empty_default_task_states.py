from django.db import migrations

DEFAULT_TASK_STATES = "open,pending"


def repair_empty_default_task_states(apps, schema_editor):
    """Restore the field default where an admin-form bug wiped it to "".

    The GlobalSetting admin form rendered the raw ``default_task_states``
    text field but persisted from the (unrendered) checkbox companion field,
    so every admin save emptied the value. An empty value made the dashboard
    skip the status filter entirely on /my, /open and /unassigned.
    """
    GlobalSetting = apps.get_model("users", "GlobalSetting")
    GlobalSetting.objects.filter(default_task_states="").update(
        default_task_states=DEFAULT_TASK_STATES
    )


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0049_ssoconfiguration"),
    ]

    operations = [
        migrations.RunPython(
            repair_empty_default_task_states, migrations.RunPython.noop
        ),
    ]
