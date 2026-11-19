from payments.models import Payment


def test_payment_has_amount_field():
    assert hasattr(Payment, "amount")
