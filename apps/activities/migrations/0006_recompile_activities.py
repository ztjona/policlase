from django.db import migrations


def recompile(apps, schema_editor):
    """Las actividades compiladas antes de policlase-gen 0.5.0 no tienen el título de cada ítem (el
    índice del intento lo toma de aquí para los intentos ya empezados) ni las diapositivas de
    solución: se recompilan desde su fuente, que no cambia. Los intentos conservan su copia; las
    claves, versiones y puntos de los ítems no cambian."""
    from policlase_gen import document as content_format

    Activity = apps.get_model("activities", "Activity")
    for activity in Activity.objects.all():
        document, report, use = content_format.load_text(activity.source)
        if document is None or report.errors or use == "class":
            continue
        activity.compiled = content_format.compile_content(document)
        activity.save(update_fields=["compiled"])


class Migration(migrations.Migration):
    dependencies = [("activities", "0005_review_window_and_parts")]
    operations = [migrations.RunPython(recompile, migrations.RunPython.noop)]
