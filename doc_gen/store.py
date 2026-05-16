"""Read and write per-file documentation under a docs output directory."""

from __future__ import annotations

import hashlib
from pathlib import Path

_FRONTMATTER_SEP = "---"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _doc_path(docs_dir: Path, filepath: str) -> Path:
    """Map a repo-relative source path to its .md doc file."""
    return docs_dir / (filepath + ".md")


def doc_exists(docs_dir: Path, filepath: str) -> bool:
    return _doc_path(docs_dir, filepath).exists()


def read_doc(docs_dir: Path, filepath: str) -> str:
    """Return the doc body, stripping frontmatter if present."""
    raw = _doc_path(docs_dir, filepath).read_text(encoding="utf-8")
    return _strip_frontmatter(raw)


def read_hash(docs_dir: Path, filepath: str) -> str | None:
    """Return the stored content hash, or None if not present."""
    raw = _doc_path(docs_dir, filepath).read_text(encoding="utf-8")
    return _parse_hash(raw)


def write_doc(docs_dir: Path, filepath: str, content: str, source_hash: str | None = None) -> None:
    path = _doc_path(docs_dir, filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    if source_hash:
        body = f"{_FRONTMATTER_SEP}\ncontent_hash: {source_hash}\n{_FRONTMATTER_SEP}\n\n{content}"
    else:
        body = content
    path.write_text(body, encoding="utf-8")


def list_docs(docs_dir: Path) -> list[str]:
    """Return all documented filepaths (relative to docs_dir, without .md suffix)."""
    if not docs_dir.exists():
        return []
    return [str(p.relative_to(docs_dir)).removesuffix(".md") for p in docs_dir.rglob("*.md")]


def _strip_frontmatter(raw: str) -> str:
    if not raw.startswith(_FRONTMATTER_SEP + "\n"):
        return raw
    end = raw.index(_FRONTMATTER_SEP + "\n", len(_FRONTMATTER_SEP))
    return raw[end + len(_FRONTMATTER_SEP) + 1:].lstrip("\n")


def _parse_hash(raw: str) -> str | None:
    if not raw.startswith(_FRONTMATTER_SEP + "\n"):
        return None
    for line in raw.splitlines():
        if line.startswith("content_hash:"):
            return line.split(":", 1)[1].strip()
    return None
