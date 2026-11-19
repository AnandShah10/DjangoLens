import unittest

from django_doctor.health_score import compute_health_score, MAX_DEDUCTION_PER_RULE


def _diag(rule_id="DJANGO001", severity="warning", confidence="static"):
    return {"rule_id": rule_id, "severity": severity, "confidence": confidence}


class HealthScoreTests(unittest.TestCase):
    def test_no_diagnostics_is_a_perfect_score(self):
        result = compute_health_score([])
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["total_diagnostics"], 0)

    def test_single_high_confidence_error_deducts_five(self):
        result = compute_health_score([_diag(severity="error", confidence="static")])
        self.assertEqual(result["score"], 95)

    def test_single_heuristic_warning_deducts_one(self):
        result = compute_health_score([_diag(severity="warning", confidence="heuristic")])
        self.assertEqual(result["score"], 99)

    def test_heuristic_diagnostics_deduct_less_than_high_confidence_ones(self):
        heuristic_result = compute_health_score([_diag(severity="error", confidence="heuristic")])
        high_result = compute_health_score([_diag(severity="error", confidence="static")])
        self.assertGreater(heuristic_result["score"], high_result["score"])

    def test_per_rule_deduction_is_capped(self):
        # 100 instances of the same heuristic warning rule would naively
        # deduct 100 points (1 each) — the cap must stop that.
        diagnostics = [_diag(rule_id="DJANGO003", severity="warning", confidence="heuristic") for _ in range(100)]
        result = compute_health_score(diagnostics)
        self.assertEqual(result["deduction_by_rule"]["DJANGO003"], MAX_DEDUCTION_PER_RULE)
        self.assertEqual(result["score"], 100 - MAX_DEDUCTION_PER_RULE)

    def test_uncapped_deduction_shows_the_real_total_even_when_capped(self):
        diagnostics = [_diag(rule_id="DJANGO003", severity="warning", confidence="heuristic") for _ in range(100)]
        result = compute_health_score(diagnostics)
        self.assertEqual(result["uncapped_deduction_by_rule"]["DJANGO003"], 100)
        self.assertEqual(result["deduction_by_rule"]["DJANGO003"], MAX_DEDUCTION_PER_RULE)

    def test_cap_applies_per_rule_not_globally(self):
        # Two different rules each hitting the cap should both count fully
        # — the cap protects against one noisy rule, not against having
        # multiple genuinely distinct problems.
        diagnostics = (
            [_diag(rule_id="DJANGO003", severity="warning", confidence="heuristic") for _ in range(100)]
            + [_diag(rule_id="DJANGO004", severity="error", confidence="static") for _ in range(100)]
        )
        result = compute_health_score(diagnostics)
        expected_deduction = MAX_DEDUCTION_PER_RULE * 2
        self.assertEqual(result["score"], max(0, 100 - expected_deduction))

    def test_score_never_goes_below_zero(self):
        diagnostics = [_diag(rule_id=f"RULE{i}", severity="error", confidence="static") for i in range(1000)]
        result = compute_health_score(diagnostics)
        self.assertEqual(result["score"], 0)

    def test_score_never_exceeds_100(self):
        # Not achievable via normal input, but the clamp should still hold
        # defensively for any deduction table tweak in the future.
        result = compute_health_score([])
        self.assertLessEqual(result["score"], 100)

    def test_unknown_severity_confidence_combo_uses_default_deduction(self):
        result = compute_health_score([_diag(severity="mystery", confidence="unknown")])
        self.assertEqual(result["score"], 99)

    def test_deduction_by_rule_only_lists_rules_that_appeared(self):
        result = compute_health_score([_diag(rule_id="DJANGO001")])
        self.assertEqual(set(result["deduction_by_rule"].keys()), {"DJANGO001"})


if __name__ == "__main__":
    unittest.main()
