from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("orders", "0005_add_priority_risky")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="notes",
            field=models.TextField(default="", blank=True),
        ),
        migrations.AddField(
            model_name="order",
            name="archived_at",
            field=models.DateTimeField(null=True),
        ),
    ]
