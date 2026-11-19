from orders.models import Order


def test_order_has_total_field():
    assert hasattr(Order, "total")
