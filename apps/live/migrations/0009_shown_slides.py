from django.db import migrations, models


def backfill(apps, schema_editor):
    """Las clases anteriores no registraron qué se proyectó: se estima con lo que sí se sabe, la
    diapositiva donde quedó la clase y las preguntas que se abrieron. Se toma todo lo visible hasta la
    más avanzada de ellas."""
    LiveSession = apps.get_model("live", "LiveSession")
    for session in LiveSession.objects.exclude(status="lobby").iterator():
        slides = session.slides or []
        if not slides:
            continue
        opened = set(session.opened or [])
        furthest = min(max([session.index, *opened]), len(slides) - 1)
        session.shown = [i for i in range(furthest + 1) if not slides[i].get("hidden") or i in opened]
        session.save(update_fields=["shown"])


class Migration(migrations.Migration):

    dependencies = [
        ("live", "0008_late_answers"),
    ]

    operations = [
        migrations.AddField(
            model_name="livesession",
            name="shown",
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
