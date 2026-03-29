"""Parse visible issue counts from DeepSource HTML pages."""

from __future__ import absolute_import

import re
from typing import List


ALL_ISSUES_JSON_PATTERN = re.compile(
    r'"all"\s*,\s*([\d.,kK]+)\s*,\s*"recommended"',
    re.IGNORECASE,
)
ISSUE_LINK_PREFIX = 'href="/gh/'
ISSUE_LINK_SUFFIX = "/occurrences?listindex=0"


def human_count_to_int(raw_value: str) -> int | None:
    """Convert a DeepSource count string into an integer."""
    value = raw_value.strip().lower().replace(",", "")
    if not value:
        return None
    multiplier = 1000 if value.endswith("k") else 1
    numeric_value = value[:-1] if multiplier != 1 else value
    try:
        return int(float(numeric_value) * multiplier + 0.999999)
    except ValueError:
        return None


def _count_from_all_issues_heading(html: str) -> int | None:
    """Extract the visible issue count from the rendered All issues heading."""
    heading_index = html.find("All issues")
    if heading_index == -1:
        return None
    window = html[heading_index : heading_index + 600]
    text_window = re.sub(r"<[^>]+>", " ", window)
    compact = " ".join(text_window.split())
    heading_match = re.search(r"All issues\s+([\d.,kK]+)", compact, re.IGNORECASE)
    if heading_match is None:
        return None
    return human_count_to_int(heading_match.group(1))


def extract_visible_issue_count(html: str) -> int | None:
    """Extract the visible issue count from a DeepSource page."""
    json_match = ALL_ISSUES_JSON_PATTERN.search(html)
    if json_match is not None:
        return human_count_to_int(json_match.group(1))
    return _count_from_all_issues_heading(html)


def extract_issue_links(html: str) -> List[str]:
    """Extract issue detail links from a DeepSource page."""
    links = set()
    search_start = 0
    while True:
        href_index = html.find(ISSUE_LINK_PREFIX, search_start)
        if href_index == -1:
            break
        value_start = href_index + len('href="')
        value_end = html.find('"', value_start)
        if value_end == -1:
            break
        candidate = html[value_start:value_end]
        if "/issue/" in candidate and candidate.endswith(ISSUE_LINK_SUFFIX):
            links.add(candidate)
        search_start = value_end + 1
    return sorted(links)
