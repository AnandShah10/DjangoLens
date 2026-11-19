from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("orders", "0004_backfill_status")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="priority",
            field=models.IntegerField(),
        ),
    ]
