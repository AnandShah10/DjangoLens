import os
import tempfile
import unittest

from django_doctor.discovery import discover_apps
from django_doctor.analyzers.migrations import analyze_migration_file, scan_migrations

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")
MIGRATIONS_DIR = os.path.join(FIXTURE_ROOT, "orders", "migrations")


class MigrationRiskAnalyzerTests(unittest.TestCase):
    def _diag_for(self, filename):
        return analyze_migration_file("orders", os.path.join(MIGRATIONS_DIR, filename))

    def test_dependency_order_resolves_out_of_filename_order_chain(self):
        # Filenames are deliberately numbered out of true dependency order
        # (0001 -> 0010 -> 0002) — naive filename-sort ordering would
        # process the AlterField (0002) before the AddField (0010) and
        # miss that 'nickname' was ever nullable. Dependency-based
        # ordering must get this right.
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "widgets")
            migrations_dir = os.path.join(app_dir, "migrations")
            os.makedirs(migrations_dir)
            open(os.path.join(migrations_dir, "__init__.py"), "w").close()
            with open(os.path.join(app_dir, "apps.py"), "w") as f:
                f.write(
                    "from django.apps import AppConfig\n\n"
                    "class WidgetsConfig(AppConfig):\n"
                    "    name = 'widgets'\n"
                )
            with open(os.path.join(migrations_dir, "0001_create_widget.py"), "w") as f:
                f.write(
                    "from django.db import migrations, models\n\n"
                    "class Migration(migrations.Migration):\n"
                    "    dependencies = []\n"
                    "    operations = [\n"
                    "        migrations.CreateModel(\n"
                    "            name='Widget',\n"
                    "            fields=[('id', models.AutoField(primary_key=True))],\n"
                    "        ),\n"
                    "    ]\n"
                )
            with open(os.path.join(migrations_dir, "0010_add_nickname.py"), "w") as f:
                f.write(
                    "from django.db import migrations, models\n\n"
                    "class Migration(migrations.Migration):\n"
                    "    dependencies = [('widgets', '0001_create_widget')]\n"
                    "    operations = [\n"
                    "        migrations.AddField(model_name='widget', name='nickname', "
                    "field=models.CharField(max_length=50, null=True)),\n"
                    "    ]\n"
                )
            with open(os.path.join(migrations_dir, "0002_require_nickname.py"), "w") as f:
                f.write(
                    "from django.db import migrations, models\n\n"
                    "class Migration(migrations.Migration):\n"
                    "    dependencies = [('widgets', '0010_add_nickname')]\n"
                    "    operations = [\n"
                    "        migrations.AlterField(model_name='widget', name='nickname', "
                    "field=models.CharField(max_length=50, null=False)),\n"
                    "    ]\n"
                )

            apps = discover_apps(tmp)
            diagnostics = scan_migrations(apps)
            nickname_diags = [d for d in diagnostics if "nickname" in d["message"]]

            self.assertEqual(len(nickname_diags), 1)
            self.assertEqual(nickname_diags[0]["confidence"], "static")
            self.assertIn("nullable to non-nullable", nickname_diags[0]["message"])

    def test_initial_create_model_has_no_diagnostics(self):
        self.assertEqual(self._diag_for("0001_initial.py"), [])

    def test_add_index_flagged_as_medium_risk(self):
        diagnostics = self._diag_for("0002_add_email_index.py")
        self.assertEqual(len(diagnostics), 1)
        diag = diagnostics[0]
        self.assertEqual(diag["rule_id"], "DJANGO004")
        self.assertEqual(diag["severity"], "warning")
        self.assertEqual(diag["confidence"], "static")
        self.assertIn("Index", diag["message"])

    def test_remove_field_flagged_as_destructive(self):
        diagnostics = self._diag_for("0003_remove_legacy_field.py")
        self.assertEqual(len(diagnostics), 1)
        diag = diagnostics[0]
        self.assertEqual(diag["severity"], "error")
        self.assertIn("Destructive", diag["message"])
        self.assertIn("RemoveField", diag["message"])

    def test_run_python_without_reverse_code_flagged_twice(self):
        # One diagnostic for irreversibility, one general "review for
        # expensive operations" note — both static, neither claims to know
        # what the function actually does at runtime.
        diagnostics = self._diag_for("0004_backfill_status.py")
        self.assertEqual(len(diagnostics), 2)
        messages = {d["message"] for d in diagnostics}
        self.assertTrue(any("Irreversible" in m for m in messages))
        self.assertTrue(any("review" in m.lower() for m in messages))

    def test_add_field_without_null_or_default_is_high_risk(self):
        diagnostics = self._diag_for("0005_add_priority_risky.py")
        self.assertEqual(len(diagnostics), 1)
        diag = diagnostics[0]
        self.assertEqual(diag["severity"], "error")
        self.assertIn("AddField", diag["message"])

    def test_add_field_with_default_or_null_true_is_not_flagged(self):
        # notes has default="", archived_at has null=True — neither should
        # be flagged; this is the false-positive check for DJANGO004/AddField.
        diagnostics = self._diag_for("0006_add_notes_safe.py")
        self.assertEqual(diagnostics, [])

    def test_alter_field_flagged_when_history_shows_it_was_nullable(self):
        # Process the sequence in order so field_state carries discount_code's
        # null=True from 0007 into 0008 — this should be a real, static-confidence flag.
        apps = discover_apps(FIXTURE_ROOT)
        diagnostics = scan_migrations(apps)
        regressions = [d for d in diagnostics if "discount_code" in d["message"]]
        self.assertEqual(len(regressions), 1)
        diag = regressions[0]
        self.assertEqual(diag["confidence"], "static")
        self.assertEqual(diag["severity"], "warning")
        self.assertIn("nullable to non-nullable", diag["message"])

    def test_alter_field_not_flagged_when_already_non_nullable(self):
        # 'total' was already non-nullable back in 0001_initial — this is
        # the concrete false-positive fix: AlterField(null=False) on an
        # already-required field must NOT be reported as a regression.
        apps = discover_apps(FIXTURE_ROOT)
        diagnostics = scan_migrations(apps)
        self.assertFalse(any(
            d["file"].endswith("0008_require_discount_code.py") and "total" in d["message"]
            for d in diagnostics
        ))

    def test_alter_field_is_heuristic_confidence_when_history_is_unknown(self):
        # Calling analyze_migration_file on 0008 in isolation (no preceding
        # CreateModel/AddField seen) means history is genuinely unknown for
        # both fields — both should still be flagged, but as 'heuristic',
        # not 'static', and the message should say history is unknown.
        diagnostics = self._diag_for("0008_require_discount_code.py")
        self.assertEqual(len(diagnostics), 2)
        for diag in diagnostics:
            self.assertEqual(diag["confidence"], "heuristic")
            self.assertIn("unknown", diag["message"])

    def test_broken_migration_file_does_not_crash_the_scan(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("class Migration(:\n    not valid python")
            path = f.name
        try:
            self.assertEqual(analyze_migration_file("orders", path), [])
        finally:
            os.unlink(path)

    def test_scan_migrations_covers_the_whole_fixture_project(self):
        apps = discover_apps(FIXTURE_ROOT)
        diagnostics = scan_migrations(apps)
        # 1 (AddIndex) + 1 (RemoveField) + 2 (RunPython) + 1 (AddField risky)
        # + 1 (discount_code nullability regression) = 6.
        # 'total' AlterField in 0008 is correctly NOT counted (see
        # test_alter_field_not_flagged_when_already_non_nullable).
        self.assertEqual(len(diagnostics), 6)
        self.assertTrue(all(d["rule_id"] == "DJANGO004" for d in diagnostics))
        self.assertTrue(all(d["app"] == "orders" for d in diagnostics))


if __name__ == "__main__":
    unittest.main()
