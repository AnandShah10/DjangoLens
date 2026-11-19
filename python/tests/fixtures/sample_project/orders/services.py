from payments.models import Payment


def charge_order(order):
    return Payment.objects.create(order=order, amount=order.total)
