import unittest

from djangolens_runtime.query_analysis import normalize_sql, analyze_events


class NormalizeSqlTests(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(normalize_sql("SELECT  *\nFROM  orders"), "SELECT * FROM orders")

    def test_strips_quoted_literals(self):
        self.assertEqual(normalize_sql("SELECT * FROM t WHERE name = 'Bob'"), "SELECT * FROM t WHERE name = ?")

    def test_strips_numeric_literals(self):
        self.assertEqual(normalize_sql("SELECT * FROM t WHERE id = 42"), "SELECT * FROM t WHERE id = ?")

    def test_none_sql_does_not_crash(self):
        self.assertEqual(normalize_sql(None), "")


class AnalyzeEventsTests(unittest.TestCase):
    def _event(self, sql, file="orders/views.py", line=10, duration=0.01):
        return {
            "sql": sql, "duration": duration, "alias": "default",
            "file": file, "line": line, "function": "order_list_view",
        }

    def test_detects_duplicate_query_from_same_location(self):
        events = [self._event("SELECT * FROM users WHERE id = %s") for _ in range(3)]
        result = analyze_events(events)
        self.assertEqual(result["total_queries"], 3)
        self.assertEqual(len(result["duplicate_groups"]), 1)
        self.assertEqual(result["duplicate_groups"][0]["count"], 3)

    def test_single_query_is_not_a_duplicate_group(self):
        result = analyze_events([self._event("SELECT * FROM orders")])
        self.assertEqual(result["duplicate_groups"], [])

    def test_same_sql_from_different_locations_is_not_grouped_together(self):
        events = [
            self._event("SELECT * FROM users WHERE id = %s", file="orders/views.py", line=10),
            self._event("SELECT * FROM users WHERE id = %s", file="payments/services.py", line=20),
        ]
        result = analyze_events(events)
        # Two different call sites, same shape by coincidence — not a
        # duplicate/N+1 group.
        self.assertEqual(result["duplicate_groups"], [])

    def test_literal_values_normalized_so_they_still_group(self):
        events = [self._event(f"SELECT * FROM users WHERE id = {i}") for i in (1, 2, 3)]
        result = analyze_events(events)
        self.assertEqual(len(result["duplicate_groups"]), 1)
        self.assertEqual(result["duplicate_groups"][0]["count"], 3)

    def test_duplicate_groups_sorted_by_count_descending(self):
        events = [self._event("SELECT a", line=1) for _ in range(2)] + \
                 [self._event("SELECT b", line=2) for _ in range(5)]
        result = analyze_events(events)
        self.assertEqual(result["duplicate_groups"][0]["count"], 5)
        self.assertEqual(result["duplicate_groups"][1]["count"], 2)

    def test_total_duration_is_summed(self):
        events = [self._event("SELECT a", duration=0.1), self._event("SELECT b", duration=0.25)]
        result = analyze_events(events)
        self.assertAlmostEqual(result["total_duration"], 0.35)

    def test_empty_events_list(self):
        result = analyze_events([])
        self.assertEqual(result["total_queries"], 0)
        self.assertEqual(result["duplicate_groups"], [])


if __name__ == "__main__":
    unittest.main()
