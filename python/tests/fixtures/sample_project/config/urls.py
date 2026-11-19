from django.urls import path, include

urlpatterns = [
    path("orders/", include("orders.urls")),
    path("payments/", include("payments.urls")),
]
