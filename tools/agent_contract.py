"""Shared low-level action vocabulary used by Agent planning.

The executor validates the plan before device actions, so all configured
planning/review Agents use the same finite action set.
"""

from __future__ import annotations


# These are deliberately low-level primitives. There is no free-form shell
# action and no action that asks the executor to reinterpret an Excel row.
ACTION_TYPES = {
    "tap_text",
    "tap_id",
    "tap_xy",
    "tap_bbox",
    "type_text",
    "key",
    "swipe",
    "rotate",
    "wait",
    "observe",
    "assert_text",
    "assert_id",
}
KEY_NAMES = {"BACK", "HOME", "ENTER", "DEL", "TAB", "ESC", "MENU"}
ORIENTATIONS = {"portrait", "landscape"}
