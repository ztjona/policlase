import secrets

from django.db import migrations, models

import apps.live.models


def fill_guest_tokens(apps, schema_editor):
    LiveSession = apps.get_model("live", "LiveSession")
    for session in LiveSession.objects.filter(guest_token__isnull=True):
        session.guest_token = secrets.token_urlsafe(12)
        session.save(update_fields=["guest_token"])


class Migration(migrations.Migration):
    dependencies = [("live", "0001_initial")]

    operations = [
        migrations.AddField(model_name="livesession", name="allow_guests",
                            field=models.BooleanField(default=False)),
        # Único en tres pasos: las sesiones existentes necesitan cada una su propio token.
        migrations.AddField(model_name="livesession", name="guest_token",
                            field=models.CharField(max_length=24, null=True)),
        migrations.RunPython(fill_guest_tokens, migrations.RunPython.noop),
        migrations.AlterField(model_name="livesession", name="guest_token",
                              field=models.CharField(default=apps.live.models.new_guest_token,
                                                     max_length=24, unique=True)),
        migrations.AddField(model_name="deck", name="github_path",
                            field=models.CharField(blank=True, max_length=400)),
        migrations.AddField(model_name="deck", name="github_sha",
                            field=models.CharField(blank=True, max_length=40)),
    ]
