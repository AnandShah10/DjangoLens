from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("orders", "0006_add_notes_safe")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="discount_code",
            field=models.CharField(max_length=20, null=True, blank=True),
        ),
    ]
