"""Round-trip + structural coverage for ``scripts.quality.marker_regions``.

Phase 3 of ``docs/QZP-V2-DESIGN.md`` §4 ships the drift-sync workflow
that swaps platform-owned regions out of consumer files without
touching user-owned surround. The parser is the backbone; these tests
pin the exact contract the workflow depends on.
"""

from __future__ import absolute_import

import unittest

from scripts.quality import marker_regions as mr


class ParseRegionsTests(unittest.TestCase):
    """``parse_regions`` returns structured records for every region."""

    def test_empty_text_returns_no_regions(self) -> None:
        """Empty input: no regions, no error."""
        self.assertEqual(mr.parse_regions(""), [])

    def test_text_without_markers_returns_no_regions(self) -> None:
        """A plain file with no markers yields an empty list."""
        self.assertEqual(mr.parse_regions("hello\nworld\n"), [])

    def test_single_region_captures_body_and_line_numbers(self) -> None:
        """The body is the exact text between BEGIN and END lines."""
        text = (
            "prelude\n"
            "# BEGIN quality-zero:alpha\n"
            "owned-line-1\n"
            "owned-line-2\n"
            "# END quality-zero:alpha\n"
            "postlude\n"
        )
        regions = mr.parse_regions(text)
        self.assertEqual(len(regions), 1)
        region = regions[0]
        self.assertEqual(region.region_id, "alpha")
        self.assertEqual(region.body, "owned-line-1\nowned-line-2\n")
        self.assertEqual(region.begin_line, 2)
        self.assertEqual(region.end_line, 5)

    def test_multiple_regions_preserve_order(self) -> None:
        """Two regions in the same file are returned in source order."""
        text = (
            "# BEGIN quality-zero:a\n"
            "A\n"
            "# END quality-zero:a\n"
            "middle\n"
            "// BEGIN quality-zero:b.nested-id\n"
            "B\n"
            "// END quality-zero:b.nested-id\n"
        )
        ids = mr.region_ids(text)
        self.assertEqual(ids, ["a", "b.nested-id"])

    def test_marker_inside_code_comment_still_recognised(self) -> None:
        """Any comment-prefix works — regex just searches for the token."""
        text = (
            "<!-- BEGIN quality-zero:html-block -->\n"
            "<p>owned</p>\n"
            "<!-- END quality-zero:html-block -->\n"
        )
        regions = mr.parse_regions(text)
        self.assertEqual(regions[0].body, "<p>owned</p>\n")

    def test_unterminated_region_raises(self) -> None:
        """A BEGIN without a matching END at EOF is an error."""
        text = "# BEGIN quality-zero:orphan\nbody\n"
        with self.assertRaises(mr.UnterminatedRegionError) as ctx:
            mr.parse_regions(text)
        self.assertIn("orphan", str(ctx.exception))

    def test_end_without_begin_raises(self) -> None:
        """A bare END is structurally wrong."""
        with self.assertRaises(mr.MismatchedRegionError):
            mr.parse_regions("# END quality-zero:floating\n")

    def test_mismatched_ids_raises(self) -> None:
        """BEGIN alpha followed by END beta is rejected."""
        text = (
            "# BEGIN quality-zero:alpha\n"
            "body\n"
            "# END quality-zero:beta\n"
        )
        with self.assertRaises(mr.MismatchedRegionError) as ctx:
            mr.parse_regions(text)
        self.assertIn("alpha", str(ctx.exception))
        self.assertIn("beta", str(ctx.exception))

    def test_nested_begin_raises(self) -> None:
        """A second BEGIN before the first END is rejected."""
        text = (
            "# BEGIN quality-zero:outer\n"
            "# BEGIN quality-zero:inner\n"
            "body\n"
            "# END quality-zero:inner\n"
            "# END quality-zero:outer\n"
        )
        with self.assertRaises(mr.NestedRegionError):
            mr.parse_regions(text)


class ReplaceRegionsTests(unittest.TestCase):
    """``replace_regions`` swaps bodies by id while preserving surround."""

    def test_empty_overrides_round_trips_exactly(self) -> None:
        """With no overrides the output is byte-identical to the input."""
        text = (
            "header\n"
            "# BEGIN quality-zero:a\n"
            "owned\n"
            "# END quality-zero:a\n"
            "trailer\n"
        )
        self.assertEqual(mr.replace_regions(text, {}), text)

    def test_single_region_replacement(self) -> None:
        """Only the body between BEGIN and END changes; everything else stays."""
        original = (
            "line-before\n"
            "# BEGIN quality-zero:alpha\n"
            "old body\n"
            "# END quality-zero:alpha\n"
            "line-after\n"
        )
        updated = mr.replace_regions(original, {"alpha": "NEW BODY\n"})
        self.assertEqual(
            updated,
            "line-before\n"
            "# BEGIN quality-zero:alpha\n"
            "NEW BODY\n"
            "# END quality-zero:alpha\n"
            "line-after\n",
        )

    def test_replacement_without_trailing_newline_adds_one(self) -> None:
        """Bodies missing a terminator still render legal files."""
        original = (
            "# BEGIN quality-zero:x\n"
            "any\n"
            "# END quality-zero:x\n"
        )
        updated = mr.replace_regions(original, {"x": "no-newline"})
        self.assertEqual(
            updated,
            "# BEGIN quality-zero:x\n"
            "no-newline\n"
            "# END quality-zero:x\n",
        )

    def test_replacement_preserves_unmatched_region(self) -> None:
        """Regions not in the override map keep their original body."""
        original = (
            "# BEGIN quality-zero:a\n"
            "A-old\n"
            "# END quality-zero:a\n"
            "# BEGIN quality-zero:b\n"
            "B-old\n"
            "# END quality-zero:b\n"
        )
        updated = mr.replace_regions(original, {"b": "B-NEW\n"})
        self.assertIn("A-old\n", updated)
        self.assertIn("B-NEW\n", updated)
        self.assertNotIn("B-old", updated)

    def test_replacement_preserves_surrounding_lines(self) -> None:
        """Content outside any region is preserved byte-for-byte."""
        original = (
            "above-1\n"
            "above-2\n"
            "# BEGIN quality-zero:core\n"
            "old\n"
            "# END quality-zero:core\n"
            "below-1\n"
            "below-2\n"
        )
        updated = mr.replace_regions(original, {"core": "new\n"})
        self.assertTrue(updated.startswith("above-1\nabove-2\n"))
        self.assertTrue(updated.endswith("below-1\nbelow-2\n"))

    def test_replacement_handles_empty_body(self) -> None:
        """An empty-string override yields a region with zero body lines."""
        original = (
            "# BEGIN quality-zero:x\n"
            "keep-above\n"
            "# END quality-zero:x\n"
        )
        updated = mr.replace_regions(original, {"x": ""})
        self.assertEqual(
            updated,
            "# BEGIN quality-zero:x\n"
            "# END quality-zero:x\n",
        )


class ConvenienceHelperTests(unittest.TestCase):
    """``region_ids`` and ``region_bodies`` are thin wrappers."""

    def test_region_ids_matches_parse(self) -> None:
        """The ids helper returns the same values as ``parse_regions``."""
        text = (
            "# BEGIN quality-zero:one\nA\n# END quality-zero:one\n"
            "# BEGIN quality-zero:two\nB\n# END quality-zero:two\n"
        )
        self.assertEqual(mr.region_ids(text), ["one", "two"])

    def test_region_bodies_maps_id_to_body(self) -> None:
        """The bodies helper returns a flat ``{id: body}`` dict."""
        text = (
            "# BEGIN quality-zero:foo\nfoo-body\n# END quality-zero:foo\n"
        )
        self.assertEqual(mr.region_bodies(text), {"foo": "foo-body\n"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
