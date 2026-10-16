"""Los nombres escritos todo en mayúsculas (o en minúsculas) pasan a mayúscula inicial."""

from django.db import migrations

from apps.accounts.names import name_case


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.only("pk", "first_name", "last_name"):
        first, last = name_case(user.first_name), name_case(user.last_name)
        if (first, last) != (user.first_name, user.last_name):
            User.objects.filter(pk=user.pk).update(first_name=first, last_name=last)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_user_theme_alter_user_role")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
