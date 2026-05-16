"""Wiki manifest loading and glob matching."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import click
import yaml


@dataclass
class WikiPage:
    title: str
    output_path: str
    sources: list[str] = field(default_factory=list)
    mermaid: bool = False
    quickstart: bool = False


def load_manifest(manifest_path: str) -> list[WikiPage]:
    """Parse wiki.yaml and return a list of WikiPage objects."""
    data = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    pages = []
    for entry in data.get("pages", []):
        if not entry.get("title") or not entry.get("output_path") or not entry.get("sources"):
            raise ValueError(f"Wiki page missing required fields (title, output_path, sources): {entry}")
        pages.append(WikiPage(
            title=entry["title"],
            output_path=entry["output_path"],
            sources=entry["sources"],
            mermaid=entry.get("mermaid", False),
            quickstart=entry.get("quickstart", False),
        ))
    return pages


def glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a glob pattern with ** support to a compiled regex."""
    result = []
    i = 0
    while i < len(pattern):
        if pattern[i:i+3] == "**/":
            result.append("(?:.*/)?")
            i += 3
        elif pattern[i:i+2] == "**":
            result.append(".*")
            i += 2
        elif pattern[i] == "*":
            result.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            result.append("[^/]")
            i += 1
        elif pattern[i] == ".":
            result.append(r"\.")
            i += 1
        else:
            result.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(result) + "$")


def resolve_sources(page: WikiPage, all_files: list[str]) -> list[str]:
    """Return files from all_files matching any of the page's source globs."""
    regexes = [glob_to_regex(p) for p in page.sources]
    return sorted(f for f in all_files if any(r.match(f) for r in regexes))


def find_affected_pages(pages: list[WikiPage], changed_files: list[str]) -> list[WikiPage]:
    """Return pages where at least one changed file matches the page's source globs."""
    return [p for p in pages if resolve_sources(p, changed_files)]
