"""Tests for DJANGO016 unregistered signal handlers."""

import os
import textwrap
import tempfile

from django_doctor.analyzers.unused_signals import scan_file_for_unused_signals


def _write(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return path


def test_unregistered_handler_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "signals.py", """
            def on_order_saved(sender, instance, **kwargs):
                pass
        """)
        diags = scan_file_for_unused_signals(path)
        assert any(d["rule_id"] == "DJANGO016" and "on_order_saved" in d["message"] for d in diags)


def test_receiver_decorated_not_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "signals.py", """
            from django.dispatch import receiver
            from django.db.models.signals import post_save

            @receiver(post_save, sender=None)
            def on_order_saved(sender, instance, **kwargs):
                pass
        """)
        diags = scan_file_for_unused_signals(path)
        assert not any("on_order_saved" in d["message"] and "unregistered" in d["message"].lower() for d in diags)


def test_connect_unknown_handler():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "signals.py", """
            from django.db.models.signals import post_save
            post_save.connect(missing_handler, sender=None)
        """)
        diags = scan_file_for_unused_signals(path)
        assert any("missing_handler" in d["message"] for d in diags)
