from django.db import migrations


def recompile(apps, schema_editor):
    """Las presentaciones guardadas antes de `hidden` y de la retroalimentación final se
    recompilan desde su fuente, que no cambia."""
    from policlase_gen import deck as deckfmt

    Deck = apps.get_model("live", "Deck")
    for deck in Deck.objects.all():
        document, report = deckfmt.load_deck_text(deck.source)
        if document is not None and not report.errors:
            deck.compiled = deckfmt.compile_deck(document)
            deck.save(update_fields=["compiled"])


class Migration(migrations.Migration):
    dependencies = [("live", "0003_pause_feedback_sections")]
    operations = [migrations.RunPython(recompile, migrations.RunPython.noop)]
