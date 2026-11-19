"""Health score (0-100): a transparent, documented aggregate of scan
results.

The original spec explicitly warns against "a meaningless arbitrary
score" and requires: explain how it's calculated, distinguish severity
from confidence, and don't let one low-confidence heuristic dominate.
This module exists to satisfy all three, not to produce an opaque number.

Formula (every constant below is the whole formula — nothing hidden
elsewhere):

    Start at 100.
    Each diagnostic deducts points based on (severity, confidence):
        error:       high/static -5,  heuristic -2
        warning:     high/static -2,  heuristic -1
        information: high/static -1,  heuristic -0.5
    Deductions are summed PER RULE ID, then each rule's total is capped
    at MAX_DEDUCTION_PER_RULE before being subtracted from 100 — so, say,
    200 instances of one heuristic N+1 warning cannot alone drag the
    score to 0; that single rule can cost at most the cap, the same
    ceiling a rule with just a handful of instances would hit.
    Final score is clamped to [0, 100].

This is one reasonable formula, not a validated industry standard — teams
that disagree with these weights should treat `deduction_by_rule` (in the
returned dict) as the actual audit trail and adjust from there, rather
than trusting the single number blindly.
"""

_DEDUCTION_BY_SEVERITY_CONFIDENCE = {
    ("error", "high"): 5, ("error", "static"): 5, ("error", "heuristic"): 2,
    ("warning", "high"): 2, ("warning", "static"): 2, ("warning", "heuristic"): 1,
    ("information", "high"): 1, ("information", "static"): 1, ("information", "heuristic"): 0.5,
}
_DEFAULT_DEDUCTION = 1  # for any (severity, confidence) combo not listed above
MAX_DEDUCTION_PER_RULE = 20


def compute_health_score(diagnostics):
    """Returns {score, total_diagnostics, deduction_by_rule,
    uncapped_deduction_by_rule, max_deduction_per_rule}."""
    uncapped_by_rule = {}
    for d in diagnostics:
        key = (d.get("severity"), d.get("confidence"))
        points = _DEDUCTION_BY_SEVERITY_CONFIDENCE.get(key, _DEFAULT_DEDUCTION)
        rule_id = d.get("rule_id", "UNKNOWN")
        uncapped_by_rule[rule_id] = uncapped_by_rule.get(rule_id, 0) + points

    capped_by_rule = {
        rule_id: min(points, MAX_DEDUCTION_PER_RULE)
        for rule_id, points in uncapped_by_rule.items()
    }
    total_deduction = sum(capped_by_rule.values())
    score = max(0, min(100, round(100 - total_deduction)))

    return {
        "score": score,
        "total_diagnostics": len(diagnostics),
        "deduction_by_rule": {k: round(v, 1) for k, v in sorted(capped_by_rule.items())},
        "uncapped_deduction_by_rule": {k: round(v, 1) for k, v in sorted(uncapped_by_rule.items())},
        "max_deduction_per_rule": MAX_DEDUCTION_PER_RULE,
    }
