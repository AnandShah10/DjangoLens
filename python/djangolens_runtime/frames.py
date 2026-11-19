"""Maps a captured stack trace back to 'the first frame that's actually
project code' — pure Python, no Django dependency, so unlike the rest of
djangolens_runtime this IS unit-tested directly (see tests/test_frames.py).
This is the core of the spec's "SQL -> source location" mapping.
"""

import os

_EXCLUDED_MARKERS = (
    f"{os.sep}site-packages{os.sep}",
    f"{os.sep}dist-packages{os.sep}",
    f"{os.sep}django{os.sep}",
)


def first_project_frame(stack_summary, project_root):
    """`stack_summary` is any sequence of frame-like objects exposing
    `.filename` (e.g. `traceback.StackSummary` or a plain list of
    `traceback.FrameSummary`). Returns the innermost (closest to where the
    query was actually issued) frame that looks like project code —
    neither inside `site-packages`/`dist-packages` nor inside Django
    itself — and is under `project_root` when one is given.

    Falls back to the innermost frame overall if nothing matches, rather
    than returning None and losing the query's location entirely; the
    caller can still show "somewhere in a third-party call" rather than
    nothing.
    """
    if not stack_summary:
        return None

    abs_root = os.path.abspath(project_root) if project_root else None

    for frame in reversed(stack_summary):
        filename = os.path.abspath(frame.filename)
        if any(marker in filename for marker in _EXCLUDED_MARKERS):
            continue
        if abs_root and not filename.startswith(abs_root):
            continue
        return frame

    return stack_summary[-1]
