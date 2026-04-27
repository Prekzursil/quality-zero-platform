"""Declining patch generator for `todo-comment` category."""
from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._declining import make_decline_generator

GENERATOR_VERSION = "todo_comment/1.0.0"
CATEGORY = "todo-comment"

generate = make_decline_generator(
    reason_text="TODO comments require human judgment to resolve",
    suggested_tier="human-only",
)
