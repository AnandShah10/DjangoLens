"""Tests for inline and config diagnostic suppression."""

import os
import textwrap
import tempfile

from django_doctor.suppressions import (
    filter_diagnostics,
    load_file_suppressions,
    parse_ignore_codes,
    analyzer_enabled,
)


def test_parse_ignore_codes():
    assert "DJANGO003" in parse_ignore_codes("# django-doctor: ignore DJANGO003")
    assert parse_ignore_codes("# django-doctor: ignore DJANGO003, DJANGO015") == {
        "DJANGO003",
        "DJANGO015",
    }
    assert "DJANGO003" in parse_ignore_codes("# djangolens: ignore DJANGO003")


def test_line_suppression_filters_diagnostic():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "views.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                textwrap.dedent(
                    """
                    # django-doctor: ignore DJANGO003
                    for order in Order.objects.all():
                        print(order.customer.name)
                    """
                )
            )
        diags = [
            {
                "rule_id": "DJANGO003",
                "analyzer": "orm",
                "file": path,
                "line": 3,
                "message": "Possible N+1",
            },
            {
                "rule_id": "DJANGO001",
                "analyzer": "architecture",
                "file": path,
                "line": 3,
                "message": "other",
            },
        ]
        kept = filter_diagnostics(diags, {})
        assert len(kept) == 1
        assert kept[0]["rule_id"] == "DJANGO001"


def test_config_suppress_and_analyzer_toggle():
    diags = [
        {"rule_id": "DJANGO012", "analyzer": "unused", "message": "x"},
        {"rule_id": "DJANGO003", "analyzer": "orm", "message": "y"},
    ]
    config = {"suppress": ["DJANGO012"], "analyzers": {"orm": False}}
    kept = filter_diagnostics(diags, config)
    assert kept == []
    assert analyzer_enabled(config, "unused") is True
    assert analyzer_enabled(config, "orm") is False
