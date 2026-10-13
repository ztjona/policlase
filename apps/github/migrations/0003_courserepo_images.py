from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("github", "0002_textos_formales"),
    ]

    operations = [
        migrations.AddField(
            model_name="courserepo",
            name="images",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
