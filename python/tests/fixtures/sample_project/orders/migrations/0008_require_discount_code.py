from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("orders", "0007_add_discount_code")]

    operations = [
        # Regression: discount_code was null=True as of 0007 -> this is a
        # real nullability change DjangoLens should be able to see.
        migrations.AlterField(
            model_name="order",
            name="discount_code",
            field=models.CharField(max_length=20, null=False),
        ),
        # NOT a regression: 'total' was already non-nullable back in
        # 0001_initial (DecimalField with no null kwarg) -> this AlterField
        # doesn't change anything and should not be flagged.
        migrations.AlterField(
            model_name="order",
            name="total",
            field=models.DecimalField(max_digits=10, decimal_places=2, null=False),
        ),
    ]
