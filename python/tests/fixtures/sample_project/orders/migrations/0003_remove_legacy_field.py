from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("orders", "0002_add_email_index")]

    operations = [
        migrations.RemoveField(model_name="order", name="legacy_note"),
    ]
