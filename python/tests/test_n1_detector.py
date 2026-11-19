import ast
import os
import tempfile
import unittest

from django_doctor.analyzers.n1_detector import scan_file_for_n_plus_one

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")
VIEWS_FILE = os.path.join(FIXTURE_ROOT, "orders", "views.py")


def _line_of(filepath, needle):
    with open(filepath, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            if needle in line:
                return i
    raise AssertionError(f"{needle!r} not found in {filepath}")


class NPlusOneTests(unittest.TestCase):
    def test_detects_n_plus_one_on_relationship_traversal_in_loop(self):
        diagnostics = scan_file_for_n_plus_one(VIEWS_FILE)
        n1_diagnostics = [d for d in diagnostics if d["rule_id"] == "DJANGO003"]

        self.assertEqual(len(n1_diagnostics), 1)
        diag = n1_diagnostics[0]
        self.assertEqual(diag["confidence"], "heuristic")
        self.assertIn("customer", diag["message"])
        self.assertGreater(diag["line"], 0)
        self.assertEqual(diag["relation"], "customer")
        self.assertGreater(diag["insert_line"], 0)

    def test_insert_point_produces_valid_and_actually_fixed_code(self):
        # This is the code-action correctness test: splice
        # `.select_related("<relation>")` in at exactly (insert_line,
        # insert_col), then confirm (a) the result is still valid Python,
        # and (b) re-scanning the fixed source no longer flags that
        # relation — i.e. the insertion point is both syntactically safe
        # and semantically the right place.
        diagnostics = scan_file_for_n_plus_one(VIEWS_FILE)
        diag = next(d for d in diagnostics if d["rule_id"] == "DJANGO003")

        with open(VIEWS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        line_idx = diag["insert_line"] - 1
        col = diag["insert_col"]
        original_line = lines[line_idx]
        lines[line_idx] = original_line[:col] + f'.select_related("{diag["relation"]}")' + original_line[col:]
        fixed_source = "".join(lines)

        # (a) still valid Python
        ast.parse(fixed_source)

        # (b) re-scanning no longer flags this relation
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(fixed_source)
            path = f.name
        try:
            new_diagnostics = scan_file_for_n_plus_one(path)
            self.assertFalse(any(d.get("relation") == "customer" for d in new_diagnostics))
        finally:
            os.unlink(path)

    def test_scalar_field_access_in_loop_is_not_flagged(self):
        # order_count_view only touches order.id (no relationship traversal),
        # so no diagnostic should fall within its line range.
        start = _line_of(VIEWS_FILE, "def order_count_view")
        end = _line_of(VIEWS_FILE, "def order_list_optimized_view")

        diagnostics = scan_file_for_n_plus_one(VIEWS_FILE)
        for d in diagnostics:
            self.assertFalse(start <= d["line"] < end)

    def test_select_related_suppresses_the_warning(self):
        code = (
            "from orders.models import Order\n\n\n"
            "def optimized_view():\n"
            "    orders = Order.objects.select_related(\"customer\").all()\n"
            "    for order in orders:\n"
            "        print(order.customer.name)\n"
            "    return orders\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            diagnostics = scan_file_for_n_plus_one(path)
            self.assertEqual(diagnostics, [])
        finally:
            os.unlink(path)

    def test_non_queryset_loop_is_ignored(self):
        code = (
            "def process(customers):\n"
            "    for customer in customers:\n"
            "        print(customer.profile.bio)\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            # `customers` doesn't visibly come from .objects/.all/.filter/
            # .exclude, so this is intentionally not flagged rather than guessed.
            diagnostics = scan_file_for_n_plus_one(path)
            self.assertEqual(diagnostics, [])
        finally:
            os.unlink(path)

    def test_broken_file_does_not_crash_the_scan(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def f(:\n    this is not valid python")
            path = f.name
        try:
            diagnostics = scan_file_for_n_plus_one(path)
            self.assertEqual(diagnostics, [])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
