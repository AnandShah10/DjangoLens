import os
import tempfile
import unittest

from django_doctor.analyzers.signals import scan_file_for_signal_risks

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")
SIGNALS_FILE = os.path.join(FIXTURE_ROOT, "orders", "signals.py")


class SignalDetectorTests(unittest.TestCase):
    def setUp(self):
        self.diagnostics = scan_file_for_signal_risks(SIGNALS_FILE)

    def test_safe_receiver_is_not_flagged(self):
        # stamp_last_touched never calls .save() and makes no external
        # calls — it must not appear anywhere in the diagnostics.
        for d in self.diagnostics:
            self.assertNotIn("stamp_last_touched", d["message"])

    def test_recursive_save_receiver_is_flagged(self):
        recursion = [d for d in self.diagnostics if "recursive" in d["message"].lower()]
        self.assertEqual(len(recursion), 1)
        diag = recursion[0]
        self.assertEqual(diag["rule_id"], "DJANGO005")
        self.assertEqual(diag["confidence"], "heuristic")
        self.assertIn("resave_order", diag["message"])
        self.assertIn("Order", diag["message"])  # sender label

    def test_network_call_receiver_is_flagged(self):
        network = [d for d in self.diagnostics if "network call" in d["message"]]
        self.assertEqual(len(network), 2)  # notify_webhook (@receiver) + handle_order_deleted (.connect())
        labels = {d["message"] for d in network}
        self.assertTrue(any("notify_webhook" in m for m in labels))
        self.assertTrue(any("handle_order_deleted" in m for m in labels))
        for diag in network:
            self.assertEqual(diag["severity"], "warning")

    def test_connect_registered_handler_is_flagged_like_a_receiver(self):
        # handle_order_deleted is registered via post_delete.connect(...),
        # not @receiver — this is the gap that was fixed: DjangoLens must
        # resolve the handler reference back to its function definition.
        connect_diags = [d for d in self.diagnostics if "handle_order_deleted" in d["message"]]
        self.assertEqual(len(connect_diags), 1)
        self.assertEqual(connect_diags[0]["confidence"], "heuristic")

    def test_total_diagnostic_count(self):
        self.assertEqual(len(self.diagnostics), 3)

    def test_broken_file_does_not_crash_the_scan(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("@receiver(post_save\ndef broken(:")
            path = f.name
        try:
            self.assertEqual(scan_file_for_signal_risks(path), [])
        finally:
            os.unlink(path)

    def test_non_receiver_function_calling_save_is_ignored(self):
        code = (
            "def regular_function(instance):\n"
            "    instance.save()\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            # Not decorated with @receiver, so this is just ordinary code —
            # DjangoLens should not treat every instance.save() as a signal risk.
            self.assertEqual(scan_file_for_signal_risks(path), [])
        finally:
            os.unlink(path)

    def test_lambda_passed_to_connect_is_checked(self):
        code = (
            "from django.db.models.signals import post_save\n\n"
            "post_save.connect(lambda sender, instance, **kwargs: instance.save(), sender=Order)\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            diagnostics = scan_file_for_signal_risks(path)
            self.assertEqual(len(diagnostics), 1)
            self.assertIn("<lambda>", diagnostics[0]["message"])
        finally:
            os.unlink(path)

    def test_connect_call_for_unknown_signal_name_is_ignored(self):
        # some_other_thing.connect(...) isn't a Django signal we recognize —
        # must not crash and must not be flagged.
        code = (
            "some_other_thing.connect(my_handler, sender=Order)\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            self.assertEqual(scan_file_for_signal_risks(path), [])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
