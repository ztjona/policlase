import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grades", "0002_manual_records"),
        ("live", "0007_session_grades"),
    ]

    operations = [
        migrations.AddField(
            model_name="manualrecord",
            name="section",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="manual_records", to="live.section", verbose_name="unidad"),
        ),
        migrations.AddField(
            model_name="manualscore",
            name="pasted_name",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
