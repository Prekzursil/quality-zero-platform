"""Budget guard for LLM fallback patch generation (per design §5.2)."""
from __future__ import absolute_import


class BudgetGuard:
    """Track and enforce the maximum number of LLM patch calls per run.

    Default cap is 10 patches (``--max-llm-patches`` CLI default).
    """

    __slots__ = ("_max_patches", "_used")

    def __init__(self, max_patches: int = 10) -> None:
        self._max_patches = max_patches
        self._used = 0

    def can_proceed(self) -> bool:
        """Return ``True`` if the budget has remaining capacity."""
        return self._used < self._max_patches

    def record_call(self) -> None:
        """Record one LLM call.  Raises ``RuntimeError`` if budget exhausted."""
        if not self.can_proceed():
            raise RuntimeError(
                f"LLM patch budget exhausted: {self._used}/{self._max_patches} used"
            )
        self._used += 1

    def remaining(self) -> int:
        """Return the number of remaining calls allowed."""
        return max(0, self._max_patches - self._used)
