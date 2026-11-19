from django.contrib import admin

from payments.models import Payment


class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "amount")


admin.site.register(Payment, PaymentAdmin)
