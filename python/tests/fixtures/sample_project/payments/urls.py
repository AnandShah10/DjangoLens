from django.urls import path


def webhook_view(request):
    return None


urlpatterns = [
    path("webhook/", webhook_view, name="payment-webhook"),
]
