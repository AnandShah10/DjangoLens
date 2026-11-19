from orders.models import Order


def order_list_view(request):
    """Iterates orders and touches the related customer for every row —
    a textbook N+1 query."""
    orders = Order.objects.all()
    for order in orders:
        print(order.customer.name)
    return orders


def order_count_view(request):
    """Only touches scalar fields on the loop variable itself — no
    relationship traversal, so this should NOT be flagged."""
    orders = Order.objects.all()
    for order in orders:
        print(order.id)
    return orders


def order_list_optimized_view(request):
    """Same access pattern as order_list_view, but select_related() is
    used — should NOT be flagged."""
    orders = Order.objects.select_related("customer").all()
    for order in orders:
        print(order.customer.name)
    return orders
