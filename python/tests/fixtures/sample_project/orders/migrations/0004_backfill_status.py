from django.db import migrations


def set_default_status(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(status=None).update(status="pending")


class Migration(migrations.Migration):

    dependencies = [("orders", "0003_remove_legacy_field")]

    operations = [
        migrations.RunPython(set_default_status),
    ]
