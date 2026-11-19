"""Tests for DJANGO015 unused URL name detection."""

import os
import textwrap
import tempfile

from django_doctor.analyzers.unused_urls import scan_unused_url_names


def _write(tmpdir, rel, content):
    path = os.path.join(tmpdir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return path


def _iter(app_path):
    for dp, _, fns in os.walk(app_path):
        for fn in fns:
            if fn.endswith(".py"):
                yield os.path.join(dp, fn)


def test_unused_name_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "shop/urls.py", """
            from django.urls import path
            from . import views
            urlpatterns = [
                path("items/", views.items, name="items-list"),
                path("gone/", views.gone, name="never-used"),
            ]
        """)
        _write(tmp, "shop/views.py", """
            from django.urls import reverse
            def items(request):
                return reverse("items-list")
            def gone(request):
                return None
        """)
        apps = [("shop", os.path.join(tmp, "shop"))]
        diags = scan_unused_url_names(apps, _iter, tmp)
        names = {d.get("url_name") for d in diags if d["rule_id"] == "DJANGO015"}
        assert "never-used" in names
        assert "items-list" not in names


def test_no_names_no_crash():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "shop/urls.py", """
            from django.urls import path
            urlpatterns = [path("x/", views.x)]
        """)
        apps = [("shop", os.path.join(tmp, "shop"))]
        diags = scan_unused_url_names(apps, _iter, tmp)
        assert diags == []
