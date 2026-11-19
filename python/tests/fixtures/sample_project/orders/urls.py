from django.urls import path

from orders.views import order_list_view, order_count_view

urlpatterns = [
    path("", order_list_view, name="order-list"),
    path("count/", order_count_view, name="order-count"),
]
