"""Tests for the Model Field Health Analyzer (DJANGO009/010/011)."""

import os
import textwrap
import tempfile

from django_doctor.analyzers.models_health import scan_file_for_model_health, scan_models_health


def _write(tmpdir, relpath, content):
    path = os.path.join(tmpdir, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return path


def test_set_null_without_null_true():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "shop/models.py", """
            from django.db import models

            class Order(models.Model):
                customer = models.ForeignKey("User", on_delete=models.SET_NULL)
        """)
        diags = scan_file_for_model_health(path)
        assert any(d["rule_id"] == "DJANGO009" and "SET_NULL" in d["message"] for d in diags)


def test_missing_on_delete():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "shop/models.py", """
            from django.db import models

            class Order(models.Model):
                customer = models.ForeignKey("User")
        """)
        diags = scan_file_for_model_health(path)
        assert any(d["rule_id"] == "DJANGO009" and "missing on_delete" in d["message"] for d in diags)


def test_cascade_on_high_value_relation():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "shop/models.py", """
            from django.db import models

            class Order(models.Model):
                customer = models.ForeignKey("User", on_delete=models.CASCADE)
        """)
        diags = scan_file_for_model_health(path)
        assert any(
            d["rule_id"] == "DJANGO009" and d["severity"] == "information" and "CASCADE" in d["message"]
            for d in diags
        )


def test_unique_with_null():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "shop/models.py", """
            from django.db import models

            class User(models.Model):
                email = models.EmailField(unique=True, null=True)
        """)
        diags = scan_file_for_model_health(path)
        assert any(d["rule_id"] == "DJANGO010" and "unique=True" in d["message"] for d in diags)


def test_charfield_null_without_blank():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "shop/models.py", """
            from django.db import models

            class User(models.Model):
                nickname = models.CharField(max_length=50, null=True)
        """)
        diags = scan_file_for_model_health(path)
        assert any(d["rule_id"] == "DJANGO010" and "null=True" in d["message"] for d in diags)


def test_fk_db_index_false():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "shop/models.py", """
            from django.db import models

            class Order(models.Model):
                customer = models.ForeignKey("User", on_delete=models.PROTECT, db_index=False)
        """)
        diags = scan_file_for_model_health(path)
        assert any(d["rule_id"] == "DJANGO011" for d in diags)


def test_clean_model_no_diagnostics():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "shop/models.py", """
            from django.db import models

            class Order(models.Model):
                customer = models.ForeignKey("User", on_delete=models.PROTECT)
                note = models.CharField(max_length=100, blank=True)
                total = models.DecimalField(max_digits=10, decimal_places=2)
        """)
        diags = scan_file_for_model_health(path)
        # PROTECT on non-high-value name, clean CharField — should be empty
        assert diags == []


def test_scan_models_health_over_apps():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "shop/models.py", """
            from django.db import models

            class Item(models.Model):
                owner = models.ForeignKey("User", on_delete=models.SET_NULL)
        """)
        apps = [("shop", os.path.join(tmp, "shop"))]

        def iter_py(app_path):
            for dirpath, _, filenames in os.walk(app_path):
                for fn in filenames:
                    if fn.endswith(".py"):
                        yield os.path.join(dirpath, fn)

        diags = scan_models_health(apps, iter_py)
        assert any(d["rule_id"] == "DJANGO009" for d in diags)
