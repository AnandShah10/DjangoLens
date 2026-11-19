from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("orders", "0001_initial")]

    operations = [
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["total"], name="order_total_idx"),
        ),
    ]
