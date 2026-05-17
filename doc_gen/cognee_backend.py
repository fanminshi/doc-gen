"""Cognee knowledge graph backend for semantic search and impact analysis."""

from __future__ import annotations

import asyncio
import os

# Map OPENAI_API_KEY to LLM_API_KEY if not already set
if "LLM_API_KEY" not in os.environ and "OPENAI_API_KEY" in os.environ:
    os.environ["LLM_API_KEY"] = os.environ["OPENAI_API_KEY"]

# Use the same model as doc-gen's per-file docs unless overridden
if "LLM_MODEL" not in os.environ:
    os.environ["LLM_MODEL"] = "gpt-5.4-mini-2026-03-17"

# Use local fastembed for embeddings — no API key or external endpoint needed
if "EMBEDDING_PROVIDER" not in os.environ:
    os.environ["EMBEDDING_PROVIDER"] = "fastembed"
if "EMBEDDING_MODEL" not in os.environ:
    os.environ["EMBEDDING_MODEL"] = "BAAI/bge-small-en-v1.5"

import cognee
from cognee import SearchType

DATASET_FILES = "doc-gen-files"
DATASET_WIKI = "doc-gen-wiki"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
    except RuntimeError:
        pass
    return asyncio.run(coro)


async def _ingest_docs(docs: list[tuple[str, str]], dataset: str = DATASET_FILES) -> None:
    texts = [f"File: {filepath}\n\n{content}" for filepath, content in docs]
    await cognee.add(texts, dataset_name=dataset)
    await cognee.cognify(datasets=[dataset])


async def _ingest_wiki_pages(pages: list[tuple[str, str]]) -> None:
    texts = [f"Wiki page: {title}\n\n{content}" for title, content in pages]
    await cognee.add(texts, dataset_name=DATASET_WIKI)
    await cognee.cognify(datasets=[DATASET_WIKI])


async def _find_affected_pages(changed_files: list[str], wiki_titles: list[str]) -> list[str]:
    """Ask the graph which wiki pages are related to the changed files."""
    files_summary = ", ".join(changed_files)
    titles_summary = ", ".join(wiki_titles)
    query = (
        f"Which of these wiki pages are about topics covered by the following source files? "
        f"Source files: {files_summary}. "
        f"Wiki pages to consider: {titles_summary}. "
        f"List only the wiki page titles that are relevant."
    )
    results = await cognee.search(
        query,
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[DATASET_FILES, DATASET_WIKI],
        top_k=len(wiki_titles),
    )
    affected = []
    for title in wiki_titles:
        if any(title.lower() in str(r).lower() for r in results):
            affected.append(title)
    return affected


async def _search(query: str, top_k: int, dataset: str | None = None) -> list[dict]:
    datasets = [dataset] if dataset else [DATASET_FILES, DATASET_WIKI]
    # Use GRAPH_COMPLETION for wiki (synthesized content), CHUNKS for file docs (granular)
    results = []
    for ds in datasets:
        try:
            query_type = SearchType.GRAPH_COMPLETION if ds == DATASET_WIKI else SearchType.CHUNKS
            raw = await cognee.search(query, query_type=query_type, datasets=[ds], top_k=top_k)
            answers = []
            for r in raw:
                if isinstance(r, dict):
                    text = r.get("search_result") or r.get("text") or str(r)
                elif hasattr(r, "text"):
                    text = r.text
                else:
                    text = str(r)
                if isinstance(text, list):
                    text = " ".join(text)
                if text and "don't have enough context" not in text:
                    answers.append(text)
            if answers:
                results.append({"source": ds, "answers": answers})
        except Exception:
            pass
    return results


def ingest_docs(docs: list[tuple[str, str]]) -> None:
    """Batch ingest per-file docs into the cognee knowledge graph."""
    _run(_ingest_docs(docs))


def ingest_wiki_pages(pages: list[tuple[str, str]]) -> None:
    """Ingest wiki pages into the cognee knowledge graph."""
    _run(_ingest_wiki_pages(pages))


def find_affected_pages(changed_files: list[str], wiki_titles: list[str]) -> list[str]:
    """Return wiki page titles affected by the changed files, via graph query."""
    return _run(_find_affected_pages(changed_files, wiki_titles))


def search(query: str, top_k: int = 10) -> list[dict]:
    """Semantic search across all ingested docs and wiki pages."""
    return _run(_search(query, top_k))
