"""Tests for DJANGO014 deprecated API detector."""

import os
import textwrap
import tempfile

from django_doctor.analyzers.deprecated_apis import scan_file_for_deprecated_apis


def _write(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return path


def test_url_import_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "urls.py", """
            from django.conf.urls import url
            urlpatterns = [url(r'^old/$', views.old)]
        """)
        diags = scan_file_for_deprecated_apis(path)
        assert any(d["rule_id"] == "DJANGO014" and "url" in d["message"] for d in diags)


def test_ugettext_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "views.py", """
            from django.utils.translation import ugettext as _
        """)
        diags = scan_file_for_deprecated_apis(path)
        assert any("ugettext" in d["message"] for d in diags)


def test_nullbooleanfield_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "models.py", """
            from django.db import models
            class M(models.Model):
                flag = models.NullBooleanField()
        """)
        diags = scan_file_for_deprecated_apis(path)
        assert any("NullBooleanField" in d["message"] for d in diags)


def test_default_file_storage_setting():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "settings.py", """
            DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"
        """)
        diags = scan_file_for_deprecated_apis(path)
        assert any("DEFAULT_FILE_STORAGE" in d["message"] for d in diags)


def test_clean_path_not_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "urls.py", """
            from django.urls import path
            urlpatterns = [path("x/", views.x)]
        """)
        diags = scan_file_for_deprecated_apis(path)
        assert diags == []
