"""Inline and config-based diagnostic suppression.

Supports:
  # django-doctor: ignore DJANGO003
  # django-doctor: ignore DJANGO003, DJANGO015
  # djangolens: ignore DJANGO003   (alias)

Suppression applies to diagnostics on the same line or the next non-empty
line (so it can sit above a statement). Also supports project config:

  {
    "suppress": ["DJANGO012", "DJANGO015"],
    "analyzers": {
      "unused": false,
      "deprecated_apis": true
    }
  }
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

_IGNORE_RE = re.compile(
    r"(?:django-doctor|djangolens)\s*:\s*ignore\s+([A-Z0-9_,\s]+)",
    re.IGNORECASE,
)


def parse_ignore_codes(comment: str) -> Set[str]:
    codes: Set[str] = set()
    for m in _IGNORE_RE.finditer(comment):
        for part in m.group(1).split(","):
            code = part.strip().upper()
            if code:
                codes.add(code)
    return codes


def load_file_suppressions(filepath: str) -> Dict[int, Set[str]]:
    """Map 1-based line number -> set of rule IDs suppressed on/near that line."""
    by_line: Dict[int, Set[str]] = defaultdict(set)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return {}

    pending: Set[str] = set()
    for i, line in enumerate(lines, start=1):
        codes = parse_ignore_codes(line)
        if codes:
            by_line[i] |= codes
            pending |= codes
        # Apply pending suppressions to the next code-bearing line
        stripped = line.strip()
        if pending and stripped and not stripped.startswith("#"):
            by_line[i] |= pending
            pending = set()
        elif stripped == "" or stripped.startswith("#"):
            # keep pending across blank/comment lines (one hop only after comment)
            pass
        else:
            pending = set()
    return by_line


def config_suppressed_rules(config: Optional[dict]) -> Set[str]:
    if not config:
        return set()
    raw = config.get("suppress") or config.get("ignore") or []
    return {str(x).strip().upper() for x in raw if str(x).strip()}


def analyzer_enabled(config: Optional[dict], analyzer_id: str, default: bool = True) -> bool:
    if not config:
        return default
    analyzers = config.get("analyzers") or {}
    if analyzer_id not in analyzers:
        return default
    return bool(analyzers[analyzer_id])


def filter_diagnostics(
    diagnostics: List[dict],
    config: Optional[dict] = None,
) -> List[dict]:
    """Drop diagnostics suppressed by config or inline comments."""
    global_suppress = config_suppressed_rules(config)
    # Cache per-file line suppressions
    file_maps: Dict[str, Dict[int, Set[str]]] = {}
    kept: List[dict] = []

    for d in diagnostics:
        rule = (d.get("rule_id") or "").upper()
        analyzer = d.get("analyzer") or ""
        if not analyzer_enabled(config, analyzer, default=True):
            continue
        if rule in global_suppress:
            continue
        filepath = d.get("file")
        line = d.get("line")
        if filepath and line:
            if filepath not in file_maps:
                file_maps[filepath] = load_file_suppressions(filepath)
            local = file_maps[filepath].get(int(line), set())
            # Also check previous line (comment immediately above)
            local |= file_maps[filepath].get(int(line) - 1, set())
            if rule in local:
                continue
        kept.append(d)
    return kept
