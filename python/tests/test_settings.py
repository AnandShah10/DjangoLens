import os
import tempfile
import unittest

from django_doctor.analyzers.settings import (
    analyze_settings_file,
    scan_settings,
    summarize_settings_files,
    _find_settings_files,
)

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")
SETTINGS_DIR = os.path.join(FIXTURE_ROOT, "config", "settings")


class SettingsDriftDetectorTests(unittest.TestCase):
    def test_finds_all_environment_files(self):
        files = _find_settings_files(FIXTURE_ROOT)
        labels = {label for label, _ in files}
        self.assertEqual(labels, {"base", "development", "staging", "production"})

    def test_base_settings_with_env_loaded_secret_is_not_flagged(self):
        diagnostics, has_hardcoded_secret = analyze_settings_file("base", os.path.join(SETTINGS_DIR, "base.py"))
        self.assertEqual(diagnostics, [])
        self.assertFalse(has_hardcoded_secret)

    def test_development_debug_true_is_not_flagged(self):
        # "development" doesn't match the production-like name hints, so
        # DEBUG=True here is expected and must not be flagged.
        diagnostics, _ = analyze_settings_file("development", os.path.join(SETTINGS_DIR, "development.py"))
        self.assertEqual(diagnostics, [])

    def test_staging_debug_true_is_flagged(self):
        diagnostics, has_hardcoded_secret = analyze_settings_file("staging", os.path.join(SETTINGS_DIR, "staging.py"))
        debug_diags = [d for d in diagnostics if "DEBUG=True" in d["message"]]
        self.assertEqual(len(debug_diags), 1)
        self.assertEqual(debug_diags[0]["severity"], "error")
        self.assertTrue(has_hardcoded_secret)

    def test_staging_hardcoded_secret_is_flagged_without_leaking_value(self):
        diagnostics, _ = analyze_settings_file("staging", os.path.join(SETTINGS_DIR, "staging.py"))
        secret_diags = [d for d in diagnostics if "SECRET_KEY" in d["message"]]
        self.assertEqual(len(secret_diags), 1)
        blob = str(secret_diags[0])
        self.assertNotIn("hardcoded-staging-secret-do-not-use", blob)

    def test_production_hardcoded_secret_and_wildcard_flagged(self):
        diagnostics, has_hardcoded_secret = analyze_settings_file("production", os.path.join(SETTINGS_DIR, "production.py"))
        self.assertTrue(has_hardcoded_secret)
        messages = {d["message"] for d in diagnostics}
        self.assertTrue(any("SECRET_KEY" in m for m in messages))
        self.assertTrue(any("wildcard" in m for m in messages))
        # DEBUG = False in production.py -> must NOT be flagged.
        self.assertFalse(any("DEBUG=True" in m for m in messages))

    def test_production_secret_value_never_appears_in_any_diagnostic(self):
        diagnostics, _ = analyze_settings_file("production", os.path.join(SETTINGS_DIR, "production.py"))
        blob = str(diagnostics)
        self.assertNotIn("hardcoded-production-secret-do-not-use", blob)

    def test_scan_settings_flags_secret_hardcoded_in_multiple_files(self):
        diagnostics = scan_settings(FIXTURE_ROOT)
        multi = [d for d in diagnostics if "hardcoded in" in d["message"] and "files" in d["message"]]
        self.assertEqual(len(multi), 1)
        self.assertIn("2 settings files", multi[0]["message"])

    def test_scan_settings_total_count_for_fixture(self):
        # staging: DEBUG=True + hardcoded SECRET_KEY (2)
        # production: hardcoded SECRET_KEY + wildcard ALLOWED_HOSTS (2)
        # cross-file: SECRET_KEY hardcoded in 2 files (1)
        diagnostics = scan_settings(FIXTURE_ROOT)
        self.assertEqual(len(diagnostics), 5)
        self.assertTrue(all(d["rule_id"] == "DJANGO006" for d in diagnostics))

    def test_broken_settings_file_does_not_crash_the_scan(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("DEBUG = (\n")
            path = f.name
        try:
            diagnostics, has_secret = analyze_settings_file("broken", path)
            self.assertEqual(diagnostics, [])
            self.assertFalse(has_secret)
        finally:
            os.unlink(path)

    def test_assignment_inside_a_function_is_not_treated_as_a_setting(self):
        code = (
            "def get_debug():\n"
            "    DEBUG = True\n"
            "    return DEBUG\n"
        )
        with tempfile.NamedTemporaryFile(suffix="_production.py", mode="w", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            env_label = os.path.splitext(os.path.basename(path))[0]
            diagnostics, _ = analyze_settings_file(env_label, path)
            # DEBUG here is a local variable inside a function, not a
            # module-level setting — must not be flagged even though the
            # filename looks production-like.
            self.assertEqual(diagnostics, [])
        finally:
            os.unlink(path)


class SummarizeSettingsFilesTests(unittest.TestCase):
    def setUp(self):
        self.summaries = {s["env_label"]: s for s in summarize_settings_files(FIXTURE_ROOT)}

    def test_finds_all_four_environment_files(self):
        self.assertEqual(set(self.summaries.keys()), {"base", "development", "staging", "production"})

    def test_base_debug_false_and_secret_not_hardcoded(self):
        base = self.summaries["base"]
        self.assertFalse(base["prod_like"])
        self.assertEqual(base["debug"], False)
        self.assertEqual(base["secret_key_hardcoded"], False)  # os.environ.get(...) call, not a literal
        self.assertEqual(base["allowed_hosts_wildcard"], False)

    def test_development_debug_true_and_secret_unknown(self):
        # development.py never assigns SECRET_KEY itself (only DEBUG and
        # ALLOWED_HOSTS) — that must show as unknown (None), not False.
        dev = self.summaries["development"]
        self.assertEqual(dev["debug"], True)
        self.assertIsNone(dev["secret_key_hardcoded"])

    def test_staging_and_production_are_prod_like_with_hardcoded_secrets(self):
        for env in ("staging", "production"):
            summary = self.summaries[env]
            self.assertTrue(summary["prod_like"])
            self.assertEqual(summary["secret_key_hardcoded"], True)

    def test_only_production_has_wildcarded_allowed_hosts(self):
        self.assertEqual(self.summaries["production"]["allowed_hosts_wildcard"], True)
        self.assertEqual(self.summaries["staging"]["allowed_hosts_wildcard"], False)

    def test_no_secret_value_appears_anywhere_in_the_summary(self):
        blob = str(summarize_settings_files(FIXTURE_ROOT))
        self.assertNotIn("hardcoded-staging-secret-do-not-use", blob)
        self.assertNotIn("hardcoded-production-secret-do-not-use", blob)

    def test_broken_file_reports_parse_error_true_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_dir = os.path.join(tmp, "settings")
            os.makedirs(settings_dir)
            with open(os.path.join(settings_dir, "broken.py"), "w") as f:
                f.write("DEBUG = (\n")
            summaries = summarize_settings_files(tmp)
            self.assertEqual(len(summaries), 1)
            self.assertTrue(summaries[0]["parse_error"])
            self.assertIsNone(summaries[0]["debug"])


class SettingsInheritanceTests(unittest.TestCase):
    """`from .base import *` inheritance: an env file that never mentions
    a setting should show the value it actually inherits."""

    def _write_settings(self, tmp, files):
        settings_dir = os.path.join(tmp, "settings")
        os.makedirs(settings_dir, exist_ok=True)
        for name, content in files.items():
            with open(os.path.join(settings_dir, name), "w") as f:
                f.write(content)

    def _by_label(self, tmp):
        return {s["env_label"]: s for s in summarize_settings_files(tmp)}

    # --- against the real fixture ---------------------------------------

    def test_fixture_env_files_extend_base(self):
        summaries = {s["env_label"]: s for s in summarize_settings_files(FIXTURE_ROOT)}
        self.assertEqual(summaries["base"]["extends"], [])
        for env in ("development", "staging", "production"):
            self.assertEqual(summaries[env]["extends"], ["base"])

    def test_development_inherits_secret_key_state_from_base(self):
        dev = {s["env_label"]: s for s in summarize_settings_files(FIXTURE_ROOT)}["development"]
        # Own-file view is unchanged: development.py never assigns SECRET_KEY.
        self.assertIsNone(dev["secret_key_hardcoded"])
        # Effective view follows the star-import: base loads it from the env.
        self.assertEqual(dev["effective"]["secret_key_hardcoded"], False)
        self.assertEqual(dev["inherited_from"]["secret_key_hardcoded"], "base")

    def test_values_a_file_assigns_itself_are_not_marked_inherited(self):
        prod = {s["env_label"]: s for s in summarize_settings_files(FIXTURE_ROOT)}["production"]
        self.assertEqual(prod["effective"]["debug"], False)
        self.assertIsNone(prod["inherited_from"]["debug"])
        self.assertEqual(prod["effective"]["allowed_hosts_wildcard"], True)
        self.assertIsNone(prod["inherited_from"]["allowed_hosts_wildcard"])

    # --- purpose-built scenarios ----------------------------------------

    def test_debug_true_is_inherited_when_env_file_never_mentions_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "from .base import *\n",
            })
            prod = self._by_label(tmp)["production"]
            self.assertIsNone(prod["debug"])  # own-file: not assigned here
            self.assertEqual(prod["effective"]["debug"], True)
            self.assertEqual(prod["inherited_from"]["debug"], "base")

    def test_inheritance_follows_a_chain_through_intermediate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {
                "base.py": "DEBUG = True\n",
                "common.py": "from .base import *\n",
                "production.py": "from .common import *\n",
            })
            prod = self._by_label(tmp)["production"]
            self.assertEqual(prod["effective"]["debug"], True)
            # Attributed to the file that actually set it, not the nearest parent.
            self.assertEqual(prod["inherited_from"]["debug"], "base")

    def test_own_override_beats_the_inherited_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "from .base import *\nDEBUG = False\n",
            })
            prod = self._by_label(tmp)["production"]
            self.assertEqual(prod["effective"]["debug"], False)
            self.assertIsNone(prod["inherited_from"]["debug"])

    def test_non_literal_assignment_stays_unknown_not_the_parents_value(self):
        # DEBUG = compute_debug() overrides base's True with something
        # unknowable. Falling back to base's True would be a confident
        # claim about a value the file explicitly replaced.
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "from .base import *\nDEBUG = compute_debug()\n",
            })
            prod = self._by_label(tmp)["production"]
            self.assertIsNone(prod["effective"]["debug"])
            self.assertIsNone(prod["inherited_from"]["debug"])

    def test_conditional_assignment_stays_unknown_not_the_parents_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "from .base import *\nif True:\n    DEBUG = False\n",
            })
            prod = self._by_label(tmp)["production"]
            self.assertIsNone(prod["effective"]["debug"])

    def test_later_star_import_overrides_an_earlier_one(self):
        # Python runs imports in order, so b's DEBUG wins over a's.
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {
                "a.py": "DEBUG = True\n",
                "b.py": "DEBUG = False\n",
                "c.py": "from .a import *\nfrom .b import *\n",
            })
            c = self._by_label(tmp)["c"]
            self.assertEqual(c["extends"], ["a", "b"])
            self.assertEqual(c["effective"]["debug"], False)
            self.assertEqual(c["inherited_from"]["debug"], "b")

    def test_import_cycle_between_settings_files_terminates(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {
                "cyc_a.py": "from .cyc_b import *\n",
                "cyc_b.py": "from .cyc_a import *\n",
            })
            summaries = self._by_label(tmp)  # must simply return
            self.assertIsNone(summaries["cyc_a"]["effective"]["debug"])

    def test_star_import_of_something_that_is_not_a_settings_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {
                "production.py": "from django.conf.global_settings import *\nDEBUG = False\n",
            })
            prod = self._by_label(tmp)["production"]
            self.assertEqual(prod["extends"], [])
            self.assertEqual(prod["effective"]["debug"], False)

    def test_parse_error_file_has_empty_inheritance_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_settings(tmp, {"broken.py": "DEBUG = (\n"})
            broken = self._by_label(tmp)["broken"]
            self.assertTrue(broken["parse_error"])
            self.assertEqual(broken["extends"], [])
            self.assertIsNone(broken["effective"]["debug"])

    def test_no_secret_value_appears_in_the_inheritance_fields(self):
        blob = str(summarize_settings_files(FIXTURE_ROOT))
        self.assertNotIn("hardcoded-staging-secret-do-not-use", blob)
        self.assertNotIn("hardcoded-production-secret-do-not-use", blob)


class InheritedRiskDiagnosticsTests(unittest.TestCase):
    """Risks that reach a production-like file ONLY via `from .base import *`.
    The Visualizer already showed these; the Drift Detector now flags them."""

    def _scan(self, tmp, files, allow_broken=False):
        settings_dir = os.path.join(tmp, "settings")
        os.makedirs(settings_dir, exist_ok=True)
        for name, content in files.items():
            with open(os.path.join(settings_dir, name), "w") as f:
                f.write(content)

        # Guard against a VACUOUS pass: a "no diagnostics" assertion means
        # nothing unless every file was actually discovered and parsed.
        summaries = summarize_settings_files(tmp)
        self.assertEqual({s["env_label"] for s in summaries}, {n[:-3] for n in files})
        if not allow_broken:
            self.assertFalse([s["env_label"] for s in summaries if s["parse_error"]])
        return scan_settings(tmp)

    @staticmethod
    def _inherited(diags):
        return [d for d in diags if "inherited" in d["message"]]

    def test_inherited_debug_true_into_prod_like_file_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "from .base import *\n",
            })
            inherited = self._inherited(diags)
            self.assertEqual(len(inherited), 1)
            d = inherited[0]
            self.assertEqual(d["rule_id"], "DJANGO006")
            self.assertEqual(d["severity"], "error")
            self.assertEqual(d["confidence"], "static")
            self.assertTrue(d["file"].endswith("production.py"))
            self.assertIn("'production'", d["message"])
            self.assertIn("'base'", d["message"])

    def test_diagnostic_points_at_the_import_that_brings_the_risk_in(self):
        # Line 1 is `import os`; the star-import is on line 2 — the
        # diagnostic must land on the import, not at the top of the file.
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "import os\nfrom .base import *\n",
            })
            self.assertEqual(self._inherited(diags)[0]["line"], 2)

    def test_chain_points_at_first_hop_and_names_the_true_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "common.py": "from .base import *\n",
                "production.py": "import os\nfrom .common import *\n",
            })
            d = self._inherited(diags)[0]
            self.assertEqual(d["line"], 2)               # production's import of `common`
            self.assertIn("from 'base'", d["message"])    # but the value originates in base

    def test_non_prod_like_file_inheriting_debug_true_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "development.py": "from .base import *\n",
            })
            self.assertEqual(self._inherited(diags), [])

    def test_explicit_override_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "from .base import *\nDEBUG = False\n",
            })
            self.assertEqual(self._inherited(diags), [])

    def test_non_literal_override_is_not_flagged(self):
        # The file replaces base's True with something unknowable — must
        # not be reported as if it still held base's value.
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "from .base import *\nDEBUG = compute_debug()\n",
            })
            self.assertEqual(self._inherited(diags), [])

    def test_conditional_assignment_is_not_flagged_as_inherited(self):
        # Diagnostic-side twin of the Visualizer's conditional test: an
        # assignment inside an `if` block means the file overrides the
        # setting in a way we can't statically resolve — never report the
        # parent's value as if it still applied.
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "production.py": "from .base import *\nif True:\n    DEBUG = False\n",
            })
            self.assertEqual(self._inherited(diags), [])

    def test_later_star_import_overriding_a_risky_one_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "safe.py": "DEBUG = False\n",
                "production.py": "from .base import *\nfrom .safe import *\n",
            })
            self.assertEqual(self._inherited(diags), [])

    def test_risky_import_coming_last_wins_and_is_flagged_at_its_own_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "safe.py": "DEBUG = False\n",
                "production.py": "from .safe import *\nfrom .base import *\n",
            })
            inherited = self._inherited(diags)
            self.assertEqual(len(inherited), 1)
            self.assertEqual(inherited[0]["line"], 2)  # the `base` import, which wins

    def test_inherited_wildcard_allowed_hosts_is_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "ALLOWED_HOSTS = ['*']\n",
                "production.py": "from .base import *\n",
            })
            inherited = self._inherited(diags)
            self.assertEqual(len(inherited), 1)
            self.assertEqual(inherited[0]["severity"], "warning")
            self.assertIn("ALLOWED_HOSTS", inherited[0]["message"])

    def test_inherited_hardcoded_secret_is_an_error_and_never_leaks_the_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "SECRET_KEY = 'super-secret-literal-value'\n",
                "production.py": "from .base import *\n",
            })
            inherited = self._inherited(diags)
            self.assertEqual(len(inherited), 1)
            self.assertEqual(inherited[0]["severity"], "error")
            self.assertNotIn("super-secret-literal-value", str(diags))

    def test_own_file_debug_true_is_not_double_reported(self):
        # staging assigns DEBUG = True itself; the existing own-file rule
        # covers it. The inherited rule must not report it a second time.
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = True\n",
                "staging.py": "from .base import *\nDEBUG = True\n",
            })
            debug_diags = [d for d in diags if "DEBUG=True" in d["message"]]
            self.assertEqual(len(debug_diags), 1)
            self.assertNotIn("inherited", debug_diags[0]["message"])

    def test_fixture_produces_no_inherited_diagnostics(self):
        # Every risky value in the fixture's prod-like files is assigned
        # by the file itself, so nothing arrives purely via inheritance.
        diags = scan_settings(FIXTURE_ROOT)
        self.assertEqual(self._inherited(diags), [])
        self.assertEqual(len(diags), 5)  # unchanged from before this feature

    def test_import_cycle_between_prod_like_files_does_not_hang(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "production.py": "from .production_b import *\n",
                "production_b.py": "from .production import *\n",
            })
            self.assertEqual(self._inherited(diags), [])

    def test_unparseable_ancestor_yields_no_diagnostic_and_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            diags = self._scan(tmp, {
                "base.py": "DEBUG = (\n",
                "production.py": "from .base import *\n",
            }, allow_broken=True)
            self.assertEqual(self._inherited(diags), [])


if __name__ == "__main__":
    unittest.main()
