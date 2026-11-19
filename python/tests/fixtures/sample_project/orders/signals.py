from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
import requests

from orders.models import Order


@receiver(post_save, sender=Order)
def stamp_last_touched(sender, instance, created, **kwargs):
    """Safe: reads/writes local state but never calls .save() again, and
    makes no external calls."""
    instance._touched = True


@receiver(post_save, sender=Order)
def resave_order(sender, instance, created, **kwargs):
    """Risky: calling instance.save() inside a post_save receiver for the
    same model risks an infinite save -> signal -> save loop."""
    instance.total = instance.total or 0
    instance.save()


@receiver(post_save, sender=Order)
def notify_webhook(sender, instance, created, **kwargs):
    """Risky: a synchronous network call inside a signal handler blocks the
    triggering save() until the external request completes."""
    requests.post("https://example.com/webhook", json={"order_id": instance.id})


def handle_order_deleted(sender, instance, **kwargs):
    """Risky, but registered imperatively via .connect() below instead of
    @receiver — still a synchronous network call inside a signal handler,
    and DjangoLens should catch this registration style too."""
    requests.post("https://example.com/order-deleted", json={"order_id": instance.id})


post_delete.connect(handle_order_deleted, sender=Order)

