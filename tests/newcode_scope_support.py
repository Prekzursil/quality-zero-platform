"""One behavioural contract for the new-code scoping block, run against BOTH gates.

``complexity_gate.py`` and ``duplication_gate.py`` each carry their own copy of
the scoping block (they are embedded as standalone scripts in a caller checkout
and cannot import each other - see ``test_newcode_scope_parity.py``). Copies mean
each one needs its own coverage, and copied *tests* would rot at different rates
than the code.

So the cases live here once and every sharing module subclasses
``NewCodeScopeContract``. That makes the parity guarantee BEHAVIOURAL as well as
byte-wise: ``test_newcode_scope_parity`` proves the two copies are identical
text, and this suite proves each copy actually behaves correctly.

``NewCodeScopeContract`` is a plain mixin, deliberately NOT a ``TestCase``
subclass: unittest discovery would otherwise collect the base itself and run
every case against ``gate = None``.
"""

from __future__ import absolute_import

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "newcode"
NEWCODE_DIFF = FIXTURES / "newcode.diff"


class NewCodeScopeContract:
    """Shared cases for one module's copy of the scoping block.

    Concrete subclasses set ``module`` to the gate module under test and inherit
    from ``unittest.TestCase`` alongside this mixin.
    """

    #: The gate module under test. Set by each concrete subclass.
    module = None

    # ── normalize_path ──────────────────────────────────────────────────────
    def test_windows_backslash_form_becomes_posix(self) -> None:
        """lizard on Windows records ``.\\src\\x.py``; CI compares against ``src/x.py``."""
        self.assertEqual(self.module.normalize_path(".\\src\\legacy_knot.py"), "src/legacy_knot.py")

    def test_jscpd_backslash_form_becomes_posix(self) -> None:
        """jscpd records ``src\\x.py`` with no leading ``./``."""
        self.assertEqual(self.module.normalize_path("src\\new_settings.py"), "src/new_settings.py")

    def test_posix_dot_slash_prefix_is_stripped(self) -> None:
        """lizard on Linux records ``./src/x.py``."""
        self.assertEqual(self.module.normalize_path("./src/clean.py"), "src/clean.py")

    def test_repeated_dot_slash_prefixes_are_all_stripped(self) -> None:
        """A doubled prefix must not survive as a mismatching key."""
        self.assertEqual(self.module.normalize_path("././src/clean.py"), "src/clean.py")

    def test_already_relative_path_is_unchanged(self) -> None:
        """The strip loop must also terminate immediately when there is no prefix."""
        self.assertEqual(self.module.normalize_path("src/clean.py"), "src/clean.py")

    def test_surrounding_quotes_and_whitespace_are_stripped(self) -> None:
        """Defensive: a hand-edited report can leave the quoting in the field."""
        self.assertEqual(self.module.normalize_path('  "src/clean.py"  '), "src/clean.py")

    def test_absolute_path_is_made_relative_to_the_root(self) -> None:
        """``jscpd --absolute`` and some lizard invocations emit absolute paths."""
        self.assertEqual(
            self.module.normalize_path("/home/runner/work/repo/repo/src/clean.py", root="/home/runner/work/repo/repo"),
            "src/clean.py",
        )

    def test_absolute_path_outside_the_root_is_left_alone(self) -> None:
        """A path the root does not prefix must not be silently truncated."""
        self.assertEqual(
            self.module.normalize_path("/elsewhere/src/clean.py", root="/home/runner"), "/elsewhere/src/clean.py"
        )

    def test_windows_root_is_normalized_before_comparison(self) -> None:
        """A backslash root must still match a backslash path."""
        self.assertEqual(
            self.module.normalize_path("D:\\work\\repo\\src\\clean.py", root="D:\\work\\repo\\"), "src/clean.py"
        )

    def test_a_relative_path_under_a_root_is_left_alone(self) -> None:
        """The root only relativizes what it actually prefixes."""
        self.assertEqual(self.module.normalize_path("src/clean.py", root="/home/runner"), "src/clean.py")

    # ── parse_added_ranges ──────────────────────────────────────────────────
    def test_real_diff_yields_one_range_per_hunk(self) -> None:
        """The recorded diff has three files and one hunk each."""
        ranges = self.module.parse_added_ranges(NEWCODE_DIFF.read_text(encoding="utf-8"))
        self.assertEqual(
            ranges,
            {
                "src/legacy_knot.py": [(48, 52)],
                "src/new_knot.go": [(1, 59)],
                "src/new_settings.py": [(1, 32)],
            },
        )

    def test_a_hunk_with_no_count_covers_a_single_line(self) -> None:
        """``@@ -12 +12 @@`` is the one-line form."""
        diff = "--- a/x.py\n+++ b/x.py\n@@ -12 +12 @@\n+value = 1\n"
        self.assertEqual(self.module.parse_added_ranges(diff), {"x.py": [(12, 12)]})

    def test_a_pure_deletion_hunk_adds_no_range(self) -> None:
        """``+40,0`` added nothing, so it cannot make anything new code."""
        diff = "--- a/x.py\n+++ b/x.py\n@@ -40,3 +40,0 @@\n-gone = 1\n"
        self.assertEqual(self.module.parse_added_ranges(diff), {})

    def test_a_deleted_file_contributes_no_ranges(self) -> None:
        """``+++ /dev/null`` has no new side to scope against."""
        diff = "diff --git a/x.py b/x.py\ndeleted file mode 100644\n--- a/x.py\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-gone = 1\n"
        self.assertEqual(self.module.parse_added_ranges(diff), {})

    def test_multiple_hunks_in_one_file_are_all_recorded(self) -> None:
        """A realistic change touches a file in several places."""
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1,0 +1,2 @@\n+a = 1\n+b = 2\n@@ -20,0 +30,1 @@\n+c = 3\n"
        self.assertEqual(self.module.parse_added_ranges(diff), {"x.py": [(1, 2), (30, 30)]})

    def test_a_hunk_before_any_file_header_is_ignored(self) -> None:
        """A truncated diff must not attribute lines to the wrong file."""
        self.assertEqual(self.module.parse_added_ranges("@@ -1,0 +1,2 @@\n+a = 1\n"), {})

    def test_a_malformed_hunk_header_is_ignored(self) -> None:
        """An unparseable ``@@`` line must not crash or invent a range."""
        diff = "--- a/x.py\n+++ b/x.py\n@@ this is not a hunk @@\n+a = 1\n"
        self.assertEqual(self.module.parse_added_ranges(diff), {})

    def test_an_added_line_that_looks_like_a_file_header_is_not_one(self) -> None:
        """Adding a line whose text starts with ``++ `` renders as ``+++ ``.

        Only a ``+++`` immediately following a ``---`` is a real file header, so
        the content line must not silently retarget every following hunk.
        """
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1,0 +1,2 @@\n+++ not/a/header.py\n+a = 1\n"
        self.assertEqual(self.module.parse_added_ranges(diff), {"x.py": [(1, 2)]})

    def test_paths_are_normalized_and_the_b_prefix_removed(self) -> None:
        """``+++ b/src/x.py`` and a bare ``+++ src/x.py`` must key identically."""
        with_prefix = self.module.parse_added_ranges("--- a/src/x.py\n+++ b/src/x.py\n@@ -0,0 +1,1 @@\n+a = 1\n")
        without_prefix = self.module.parse_added_ranges("--- src/x.py\n+++ src/x.py\n@@ -0,0 +1,1 @@\n+a = 1\n")
        self.assertEqual(with_prefix, {"src/x.py": [(1, 1)]})
        self.assertEqual(without_prefix, {"src/x.py": [(1, 1)]})

    def test_a_tab_separated_header_timestamp_is_dropped(self) -> None:
        """``git diff`` omits it, but ``diff -u`` appends a tab and a timestamp."""
        diff = "--- a/x.py\t2026-08-11\n+++ b/x.py\t2026-08-11\n@@ -0,0 +1,1 @@\n+a = 1\n"
        self.assertEqual(self.module.parse_added_ranges(diff), {"x.py": [(1, 1)]})

    def test_crlf_diff_parses_identically(self) -> None:
        """A diff captured on Windows carries CRLF; a \\n-only parser reads zero hunks."""
        text = NEWCODE_DIFF.read_text(encoding="utf-8").replace("\n", "\r\n")
        self.assertEqual(self.module.parse_added_ranges(text)["src/new_knot.go"], [(1, 59)])

    # ── spans_overlap ───────────────────────────────────────────────────────
    def test_a_span_with_no_ranges_in_its_file_is_untouched(self) -> None:
        """The scan must terminate to ``False`` rather than raising on a missing key."""
        self.assertFalse(self.module.spans_overlap("src/a.py", 1, 10, {}))

    def test_a_hunk_inside_the_span_overlaps(self) -> None:
        """The ordinary case: the change edited the body of this span."""
        self.assertTrue(self.module.spans_overlap("src/a.py", 1, 10, {"src/a.py": [(5, 6)]}))

    def test_a_hunk_after_the_span_does_not_overlap(self) -> None:
        """The recorded ``classify`` (4-42) against the real hunk at 48-52."""
        self.assertFalse(self.module.spans_overlap("src/a.py", 4, 42, {"src/a.py": [(48, 52)]}))

    def test_a_hunk_before_the_span_does_not_overlap(self) -> None:
        """Symmetric case, so the comparison cannot be one-sided."""
        self.assertFalse(self.module.spans_overlap("src/a.py", 40, 50, {"src/a.py": [(1, 3)]}))

    def test_a_hunk_overlapping_the_first_line_counts(self) -> None:
        """Boundary: the range ends exactly on the span's first line."""
        self.assertTrue(self.module.spans_overlap("src/a.py", 10, 20, {"src/a.py": [(5, 10)]}))

    def test_a_hunk_overlapping_the_last_line_counts(self) -> None:
        """Boundary: the range starts exactly on the span's last line."""
        self.assertTrue(self.module.spans_overlap("src/a.py", 10, 20, {"src/a.py": [(20, 25)]}))

    def test_a_later_range_still_counts_after_an_earlier_miss(self) -> None:
        """The scan must keep going instead of stopping at the first miss."""
        self.assertTrue(self.module.spans_overlap("src/a.py", 10, 20, {"src/a.py": [(1, 2), (15, 16)]}))

    def test_a_range_in_a_different_file_does_not_overlap(self) -> None:
        """Keys are per-file; a same-numbered hunk elsewhere is irrelevant."""
        self.assertFalse(self.module.spans_overlap("src/a.py", 1, 10, {"src/b.py": [(1, 10)]}))
