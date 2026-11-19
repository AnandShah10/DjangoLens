"""Tests for unused-model / missing-admin detection (DJANGO012 / DJANGO013)."""

import os
import textwrap
import tempfile

from django_doctor.analyzers.unused import scan_unused


def _write(tmpdir, relpath, content):
    path = os.path.join(tmpdir, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return path


def _iter_py(app_path):
    for dirpath, _, filenames in os.walk(app_path):
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def test_unused_model_detected():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "shop/models.py", """
            from django.db import models

            class Orphan(models.Model):
                name = models.CharField(max_length=50)

            class Used(models.Model):
                name = models.CharField(max_length=50)
        """)
        _write(tmp, "shop/views.py", """
            from shop.models import Used

            def list_used(request):
                return Used.objects.all()
        """)
        apps = [("shop", os.path.join(tmp, "shop"))]
        diags = scan_unused(apps, _iter_py)
        unused = [d for d in diags if d["rule_id"] == "DJANGO012"]
        names = {d["model"] for d in unused}
        assert "Orphan" in names
        assert "Used" not in names


def test_missing_admin_registration():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "shop/models.py", """
            from django.db import models

            class Product(models.Model):
                name = models.CharField(max_length=50)
        """)
        _write(tmp, "shop/admin.py", """
            from django.contrib import admin
            # nothing registered
        """)
        apps = [("shop", os.path.join(tmp, "shop"))]
        diags = scan_unused(apps, _iter_py)
        missing = [d for d in diags if d["rule_id"] == "DJANGO013"]
        assert any(d["model"] == "Product" for d in missing)


def test_registered_model_not_flagged_for_admin():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "shop/models.py", """
            from django.db import models

            class Product(models.Model):
                name = models.CharField(max_length=50)
        """)
        _write(tmp, "shop/admin.py", """
            from django.contrib import admin
            from shop.models import Product

            admin.site.register(Product)
        """)
        # Also reference Product so DJANGO012 doesn't fire
        _write(tmp, "shop/views.py", """
            from shop.models import Product
        """)
        apps = [("shop", os.path.join(tmp, "shop"))]
        diags = scan_unused(apps, _iter_py)
        missing = [d for d in diags if d["rule_id"] == "DJANGO013" and d["model"] == "Product"]
        assert missing == []
