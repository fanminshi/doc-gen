"""CLI entry point: init / update / show."""

from __future__ import annotations

from pathlib import Path

import click

from doc_gen import generator, store
from doc_gen import manifest as manifest_mod
from doc_gen import wiki as wiki_mod
from doc_gen.git import (
    get_changed_files,
    get_commit_diffs,
    get_file_content,
    get_file_diff_between_refs,
    list_all_files,
    list_source_files,
    open_repo,
)

DEFAULT_DOCS_DIR = "docs"
DEFAULT_EXTENSIONS = (".py",)


def _is_test(filepath: str) -> bool:
    name = Path(filepath).name
    return name.endswith("_test.go") or name.startswith("test_") or name.endswith("_test.py")


@click.group()
def cli() -> None:
    """doc-gen: incremental LLM documentation from git history."""


@cli.command()
@click.argument("repo_path", default=".")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True, help="Output directory for docs")
@click.option("--ext", multiple=True, default=DEFAULT_EXTENSIONS, show_default=True, help="File extensions to document")
@click.option("--overwrite", is_flag=True, default=False, help="Regenerate docs even if they already exist")
@click.option("--ref", default="HEAD", show_default=True, help="Git ref (commit SHA, branch, tag) to init from")
@click.option("--skip-tests", is_flag=True, default=False, help="Skip test files")
@click.option("--prefix", default="", show_default=False, help="Only document files under this path prefix (e.g. src/)")
def init(repo_path: str, docs_dir: str, ext: tuple[str, ...], overwrite: bool, ref: str, skip_tests: bool, prefix: str) -> None:
    """Generate base documentation for all source files in REPO_PATH."""
    docs = Path(docs_dir)
    extensions = tuple(e if e.startswith(".") else f".{e}" for e in ext)

    files = list_source_files(repo_path, extensions, ref=ref)
    if prefix:
        files = [f for f in files if f.startswith(prefix)]
    if skip_tests:
        files = [f for f in files if not _is_test(f)]
    if not files:
        click.echo("No source files found.")
        return

    click.echo(f"Found {len(files)} source files at {ref}. Generating docs...")

    for filepath in files:
        if not overwrite and store.doc_exists(docs, filepath):
            click.echo(f"  skip  {filepath} (already documented)")
            continue

        click.echo(f"  init  {filepath}")
        content = get_file_content(repo_path, filepath, ref=ref)
        if not content.strip():
            click.echo(f"        (empty, skipping)")
            continue

        if not overwrite and store.doc_exists(docs, filepath):
            if store.read_hash(docs, filepath) == store.content_hash(content):
                click.echo(f"  skip  {filepath} (content unchanged)")
                continue

        doc = generator.generate_base_doc(filepath, content)
        store.write_doc(docs, filepath, doc, source_hash=store.content_hash(content))

    repo = open_repo(repo_path)
    store.write_version(docs, repo_path, repo.head.commit.hexsha, ref=ref)
    click.echo(f"\nDone. Docs written to {docs}/")


@cli.command()
@click.argument("repo_path", default=".")
@click.argument("sha")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True, help="Docs directory to update")
@click.option("--ext", multiple=True, default=DEFAULT_EXTENSIONS, show_default=True, help="File extensions to document")
@click.option("--skip-tests", is_flag=True, default=False, help="Skip test files")
def update(repo_path: str, sha: str, docs_dir: str, ext: tuple[str, ...], skip_tests: bool) -> None:
    """Update docs for all files changed in commit SHA."""
    docs = Path(docs_dir)
    extensions = tuple(e if e.startswith(".") else f".{e}" for e in ext)

    diffs = get_commit_diffs(repo_path, sha, extensions)
    if skip_tests:
        diffs = [d for d in diffs if not _is_test(d.path)]
    if not diffs:
        click.echo(f"No relevant diffs in {sha[:8]}.")
        return

    click.echo(f"Updating docs for {len(diffs)} file(s) changed in {sha[:8]}...")

    for file_diff in diffs:
        filepath = file_diff.path
        click.echo(f"  update  {filepath}")

        if not store.doc_exists(docs, filepath):
            # File is new or was never documented — generate from full content
            content = file_diff.after
            if not content.strip():
                continue
            doc = generator.generate_base_doc(filepath, content)
        else:
            if store.read_hash(docs, filepath) == store.content_hash(file_diff.after):
                click.echo(f"        (content unchanged, skipping)")
                continue
            existing_doc = store.read_doc(docs, filepath)
            if not file_diff.diff.strip():
                continue
            doc = generator.update_doc_from_diff(filepath, existing_doc, file_diff.diff)

        store.write_doc(docs, filepath, doc, source_hash=store.content_hash(file_diff.after))

    click.echo(f"\nDone.")


@cli.command()
@click.argument("repo_path", default=".")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True, help="Docs directory")
@click.option("--ref", default="HEAD", show_default=True, help="Git ref to hash file content against")
def rehash(repo_path: str, docs_dir: str, ref: str) -> None:
    """Stamp existing docs with a content hash without regenerating them."""
    docs = Path(docs_dir)
    documented = store.list_docs(docs)
    if not documented:
        click.echo("No docs found.")
        return

    click.echo(f"Rehashing {len(documented)} docs...")
    for filepath in documented:
        content = get_file_content(repo_path, filepath, ref=ref)
        if not content.strip():
            click.echo(f"  skip  {filepath} (empty)")
            continue
        existing_doc = store.read_doc(docs, filepath)
        store.write_doc(docs, filepath, existing_doc, source_hash=store.content_hash(content))
        click.echo(f"  hashed  {filepath}")

    click.echo("\nDone.")


@cli.command()
@click.argument("filepath")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True, help="Docs directory")
def show(filepath: str, docs_dir: str) -> None:
    """Print the documentation for FILEPATH."""
    docs = Path(docs_dir)
    if not store.doc_exists(docs, filepath):
        click.echo(f"No documentation found for {filepath}. Run `doc-gen init` first.")
        raise SystemExit(1)
    click.echo(store.read_doc(docs, filepath))


def _local_search(term: str, repo_path: str, docs_dir: str, top_k: int) -> str:
    results = store.local_search(term, repo_path, docs_dir, top_k)
    if not results:
        return f"No relevant documentation found for '{term}'."
    return "\n\n---\n\n".join(f"**{r['title']}**\n\n{r['excerpt']}" for r in results)


@cli.command()
@click.argument("term")
@click.argument("repo_path", default=".")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True, help="Docs directory")
@click.option("--top-k", default=5, show_default=True, help="Number of docs to retrieve")
def query(term: str, repo_path: str, docs_dir: str, top_k: int) -> None:
    """Answer a question from the wiki pages, falling back to per-file docs."""
    click.echo(_local_search(term, repo_path, docs_dir, top_k))


@cli.command()
@click.argument("repo_path", default=".")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True)
@click.option("--ext", multiple=True, default=DEFAULT_EXTENSIONS, show_default=True)
@click.option("--from-ref", required=True, help="Git ref to start replay from (e.g. a commit SHA)")
@click.option("--skip-tests", is_flag=True, default=False, help="Skip test files")
def replay(repo_path: str, docs_dir: str, ext: tuple[str, ...], from_ref: str, skip_tests: bool) -> None:
    """Update docs using a single composite diff from FROM_REF to HEAD per file."""
    docs = Path(docs_dir)
    extensions = tuple(e if e.startswith(".") else f".{e}" for e in ext)

    repo = open_repo(repo_path)
    base_sha = repo.commit(from_ref).hexsha

    files = list_source_files(repo_path, extensions, ref="HEAD")
    if skip_tests:
        files = [f for f in files if not _is_test(f)]

    click.echo(f"Applying composite diff from {base_sha[:8]} to HEAD for {len(files)} files...\n")

    for filepath in files:
        current_content = get_file_content(repo_path, filepath, ref="HEAD")
        if not current_content.strip():
            continue

        current_hash = store.content_hash(current_content)

        if store.doc_exists(docs, filepath):
            if store.read_hash(docs, filepath) == current_hash:
                click.echo(f"  skip    {filepath} (unchanged)")
                continue
            existing_doc = store.read_doc(docs, filepath)
            file_diff = get_file_diff_between_refs(repo_path, filepath, base_sha)
            if file_diff is None or not file_diff.diff.strip():
                click.echo(f"  skip    {filepath} (no diff)")
                continue
            click.echo(f"  update  {filepath}")
            doc = generator.update_doc_from_diff(filepath, existing_doc, file_diff.diff)
        else:
            click.echo(f"  init    {filepath}")
            doc = generator.generate_base_doc(filepath, current_content)

        store.write_doc(docs, filepath, doc, source_hash=current_hash)

    click.echo(f"\nDone. Docs written to {docs}/")


@cli.command("wiki-init")
@click.argument("repo_path", default=".")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True)
@click.option("--manifest", "manifest_path", default="wiki.yaml", show_default=True)
@click.option("--overwrite", is_flag=True, default=False, help="Regenerate even if unchanged")
def wiki_init(repo_path: str, docs_dir: str, manifest_path: str, overwrite: bool) -> None:
    """Generate wiki pages for all entries in the manifest."""
    docs = Path(docs_dir)
    pages = manifest_mod.load_manifest(str(Path(repo_path) / manifest_path))
    all_files = list_all_files(repo_path)

    click.echo(f"Generating {len(pages)} wiki pages...\n")

    for page in pages:
        click.echo(f"[wiki] {page.title}")
        resolved = manifest_mod.resolve_sources(page, all_files)
        if not resolved:
            click.echo(f"  warn  no source files matched — skipping")
            continue

        file_docs = []
        for f in resolved:
            if store.doc_exists(docs, f):
                file_docs.append((f, store.read_doc(docs, f)))
            else:
                raw = get_file_content(repo_path, f)
                if raw.strip():
                    file_docs.append((f, raw))

        if not file_docs:
            click.echo(f"  warn  no content found for any source file — skipping")
            continue

        source_hash = store.content_hash("\n".join(doc for _, doc in file_docs))
        output_key = page.output_path.removesuffix(".md")

        if not overwrite and store.doc_exists(Path(repo_path), output_key):
            if store.read_hash(Path(repo_path), output_key) == source_hash:
                click.echo(f"  skip  (unchanged)")
                continue

        click.echo(f"  generating from {len(file_docs)} file docs...")
        doc = wiki_mod.generate_wiki_page(page.title, file_docs, with_diagram=page.mermaid, quickstart=page.quickstart)
        store.write_doc(Path(repo_path), output_key, doc, source_hash=source_hash)
        click.echo(f"  wrote  {page.output_path}")

    click.echo(f"\nDone.")


@cli.command("wiki-update")
@click.argument("repo_path", default=".")
@click.argument("sha")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True)
@click.option("--manifest", "manifest_path", default="wiki.yaml", show_default=True)
@click.option("--use-cognee", is_flag=True, default=False, help="Use cognee graph to find affected pages instead of manifest globs")
def wiki_update(repo_path: str, sha: str, docs_dir: str, manifest_path: str, use_cognee: bool) -> None:
    """Regenerate wiki pages affected by files changed in commit SHA."""
    docs = Path(docs_dir)
    pages = manifest_mod.load_manifest(str(Path(repo_path) / manifest_path))
    changed = get_changed_files(repo_path, sha)

    if use_cognee:
        from doc_gen import cognee_backend
        click.echo(f"Using cognee graph to find affected pages for {sha[:8]}...")
        wiki_titles = [p.title for p in pages]
        affected_titles = set(cognee_backend.find_affected_pages(changed, wiki_titles))
        affected = [p for p in pages if p.title in affected_titles]
    else:
        affected = manifest_mod.find_affected_pages(pages, changed)

    if not affected:
        click.echo(f"No wiki pages affected by {sha[:8]}.")
        return

    click.echo(f"{len(affected)} wiki page(s) affected by {sha[:8]}...\n")
    all_files = list_all_files(repo_path)

    for page in affected:
        click.echo(f"[wiki] {page.title}")
        resolved = manifest_mod.resolve_sources(page, all_files)
        resolved_set = set(resolved)
        changed_in_page = [f for f in changed if f in resolved_set]

        updated_file_docs = [(f, store.read_doc(docs, f)) for f in changed_in_page if store.doc_exists(docs, f)]
        if not updated_file_docs:
            click.echo(f"  warn  changed files not yet documented — skipping")
            continue

        output_key = page.output_path.removesuffix(".md")
        if not store.doc_exists(Path(repo_path), output_key):
            click.echo(f"  init  (no existing wiki page, generating from scratch)")
            all_file_docs = [(f, store.read_doc(docs, f)) for f in resolved if store.doc_exists(docs, f)]
            doc = wiki_mod.generate_wiki_page(page.title, all_file_docs, with_diagram=page.mermaid)
        else:
            current_doc = store.read_doc(Path(repo_path), output_key)
            click.echo(f"  updating from {len(updated_file_docs)} changed file doc(s)...")
            doc = wiki_mod.update_wiki_page(page.title, current_doc, updated_file_docs)

        all_file_docs = [(f, store.read_doc(docs, f)) for f in resolved if store.doc_exists(docs, f)]
        source_hash = store.content_hash("\n".join(d for _, d in all_file_docs))
        store.write_doc(Path(repo_path), output_key, doc, source_hash=source_hash)
        click.echo(f"  wrote  {page.output_path}")

    click.echo(f"\nDone.")


@cli.command("cognee-ingest")
@click.argument("repo_path", default=".")
@click.option("--docs-dir", default=DEFAULT_DOCS_DIR, show_default=True)
@click.option("--manifest", "manifest_path", default="wiki.yaml", show_default=True)
def cognee_ingest(repo_path: str, docs_dir: str, manifest_path: str) -> None:
    """Ingest all existing docs and wiki pages into the cognee knowledge graph."""
    from doc_gen import cognee_backend

    docs = Path(docs_dir)
    documented = store.list_docs(docs)
    if not documented:
        click.echo("No docs found. Run `doc-gen init` first.")
        return

    click.echo(f"Ingesting {len(documented)} file docs into cognee...")
    file_doc_pairs = [(f, store.read_doc(docs, f)) for f in documented]
    cognee_backend.ingest_docs(file_doc_pairs)
    click.echo("  file docs ingested.")

    try:
        pages = manifest_mod.load_manifest(str(Path(repo_path) / manifest_path))
        wiki_pairs = []
        for page in pages:
            output_key = page.output_path.removesuffix(".md")
            if store.doc_exists(Path(repo_path), output_key):
                wiki_pairs.append((page.title, store.read_doc(Path(repo_path), output_key)))
        if wiki_pairs:
            click.echo(f"Ingesting {len(wiki_pairs)} wiki pages into cognee...")
            cognee_backend.ingest_wiki_pages(wiki_pairs)
            click.echo("  wiki pages ingested.")
    except FileNotFoundError:
        click.echo("  (no wiki.yaml found, skipping wiki page ingestion)")

    click.echo("\nDone. Knowledge graph is ready.")


@cli.command("cognee-search")
@click.argument("query")
@click.option("--top-k", default=5, show_default=True, help="Number of results to return")
def cognee_search(query: str, top_k: int) -> None:
    """Semantic search across all ingested docs using the cognee knowledge graph."""
    from doc_gen import cognee_backend

    click.echo(f"Searching: {query}\n")
    results = cognee_backend.search(query, top_k=top_k)
    if not results:
        click.echo("No results found.")
        return
    for group in results:
        source = "Wiki" if group["source"] == "doc-gen-wiki" else "File Docs"
        click.echo(f"── {source} ──")
        for answer in group["answers"]:
            click.echo(f"{answer}\n")
