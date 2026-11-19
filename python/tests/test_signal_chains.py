import os
import tempfile
import unittest

from django_doctor.discovery import discover_apps
from django_doctor.indexer import iter_python_files
from django_doctor.analyzers.signals import find_signal_chains, scan_signal_chains

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")
SIGNALS_FILE = os.path.join(FIXTURE_ROOT, "orders", "signals.py")


class FindSignalChainsTests(unittest.TestCase):
    def setUp(self):
        self.chains = find_signal_chains(SIGNALS_FILE)

    def test_finds_every_receiver_not_just_risky_ones(self):
        # 4 receivers total in the fixture: one safe, two risky via
        # @receiver, one risky via .connect() — unlike diagnostics, this
        # must include the safe one too.
        self.assertEqual(len(self.chains), 4)
        receivers = {c["receiver"] for c in self.chains}
        self.assertIn("stamp_last_touched", receivers)  # the safe one

    def test_safe_receiver_has_no_effects(self):
        safe = next(c for c in self.chains if c["receiver"] == "stamp_last_touched")
        self.assertEqual(safe["effects"], [])
        self.assertEqual(safe["signal"], "post_save")
        self.assertEqual(safe["sender"], "Order")

    def test_recursive_save_receiver_has_recursive_save_effect(self):
        risky = next(c for c in self.chains if c["receiver"] == "resave_order")
        self.assertEqual(risky["effects"], ["recursive_save"])

    def test_network_call_receiver_has_network_call_effect(self):
        risky = next(c for c in self.chains if c["receiver"] == "notify_webhook")
        self.assertEqual(risky["effects"], ["network_call"])

    def test_connect_registered_receiver_is_included_with_correct_signal(self):
        connect_registered = next(c for c in self.chains if c["receiver"] == "handle_order_deleted")
        self.assertEqual(connect_registered["signal"], "post_delete")
        self.assertEqual(connect_registered["sender"], "Order")
        self.assertEqual(connect_registered["effects"], ["network_call"])

    def test_every_chain_has_a_location(self):
        for chain in self.chains:
            self.assertTrue(chain["file"].endswith("signals.py"))
            self.assertGreater(chain["line"], 0)

    def test_broken_file_returns_empty_list_not_a_crash(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("@receiver(post_save\ndef broken(:")
            path = f.name
        try:
            self.assertEqual(find_signal_chains(path), [])
        finally:
            os.unlink(path)

    def test_lambda_handler_has_lambda_placeholder_receiver_name(self):
        code = (
            "from django.db.models.signals import post_save\n\n"
            "post_save.connect(lambda sender, instance, **kwargs: None, sender=Order)\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            chains = find_signal_chains(path)
            self.assertEqual(len(chains), 1)
            self.assertEqual(chains[0]["receiver"], "<lambda>")
        finally:
            os.unlink(path)


class ScanSignalChainsTests(unittest.TestCase):
    def test_covers_whole_fixture_project(self):
        apps = discover_apps(FIXTURE_ROOT)
        chains = scan_signal_chains(apps, iter_python_files)
        self.assertEqual(len(chains), 4)
        self.assertTrue(all(c["file"].endswith("signals.py") for c in chains))


if __name__ == "__main__":
    unittest.main()
