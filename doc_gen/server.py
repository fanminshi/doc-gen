"""FastAPI web server for doc-gen UI."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any
import re

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from doc_gen import generator, manifest as manifest_mod, store, wiki as wiki_mod

try:
    from doc_gen import cognee_backend
    _COGNEE_AVAILABLE = True
except ImportError:
    cognee_backend = None  # type: ignore
    _COGNEE_AVAILABLE = False
from doc_gen.git import (
    get_changed_files,
    get_commit_diffs,
    get_file_content,
    list_all_files,
    list_source_files,
    open_repo,
)

_LANG_EXTENSIONS = {
    ".go": [".go"],
    ".py": [".py"],
    ".ts": [".ts", ".tsx"],
    ".js": [".js", ".jsx"],
    ".rs": [".rs"],
    ".java": [".java"],
    ".rb": [".rb"],
    ".cs": [".cs"],
    ".cpp": [".cpp", ".cc", ".cxx", ".h", ".hpp"],
    ".swift": [".swift"],
    ".kt": [".kt"],
}
_SKIP_DIRS = {".git", "vendor", "node_modules", "dist", "build", "__pycache__", ".venv", "venv"}


def _get_github_info(repo_path: str) -> dict | None:
    """Extract GitHub owner/repo from git remote."""
    result = subprocess.run(
        ["git", "-C", repo_path, "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    url = result.stdout.strip()
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    return {
        "owner": owner,
        "repo": repo,
        "wiki_url": f"https://github.com/{owner}/{repo}/wiki",
        "wiki_git": f"https://github.com/{owner}/{repo}.wiki.git",
    }


def _push_to_github_wiki(repo_path: str, wiki_dir: str) -> str | None:
    """Clone/pull the GitHub wiki repo, copy pages, commit and push. Returns error or None."""
    info = _get_github_info(repo_path)
    if not info:
        return "No GitHub remote found"

    wiki_clone = Path("/tmp") / f"{info['repo']}-wiki-push"
    wiki_source = Path(wiki_dir)

    if not wiki_source.exists() or not any(wiki_source.glob("*.md")):
        return "No wiki pages found to push"

    try:
        if wiki_clone.exists():
            subprocess.run(["git", "-C", str(wiki_clone), "pull"], check=True, capture_output=True)
        else:
            subprocess.run(["git", "clone", info["wiki_git"], str(wiki_clone)], check=True, capture_output=True)

        for md in wiki_source.glob("*.md"):
            import shutil
            shutil.copy(md, wiki_clone / md.name)

        subprocess.run(["git", "-C", str(wiki_clone), "add", "."], check=True, capture_output=True)
        result = subprocess.run(
            ["git", "-C", str(wiki_clone), "diff", "--cached", "--quiet"],
            capture_output=True,
        )
        if result.returncode == 0:
            return None  # nothing to push

        subprocess.run(
            ["git", "-C", str(wiki_clone), "commit", "-m", "doc-gen: update wiki"],
            check=True, capture_output=True,
        )
        subprocess.run(["git", "-C", str(wiki_clone), "push"], check=True, capture_output=True)
        return None
    except subprocess.CalledProcessError as e:
        return e.stderr.decode() if e.stderr else str(e)


def _detect_extensions(repo_path: str) -> list[str]:
    """Count source files by extension and return the dominant language's extensions."""
    counts: dict[str, int] = {}
    root = Path(repo_path)
    for f in root.rglob("*"):
        if any(p in _SKIP_DIRS for p in f.parts):
            continue
        if f.is_file():
            counts[f.suffix] = counts.get(f.suffix, 0) + 1
    if not counts:
        return [".py"]
    top = max(
        (ext for ext in counts if ext in _LANG_EXTENSIONS),
        key=lambda e: counts[e],
        default=None,
    )
    return _LANG_EXTENSIONS.get(top, [".py"])


app = FastAPI(title="doc-gen")


@app.get("/project/detect")
async def detect_language(repo_path: str) -> dict:
    if not Path(repo_path).is_dir():
        return {"error": "Path does not exist"}
    extensions = _detect_extensions(repo_path)
    return {"extensions": extensions}

# ── State ────────────────────────────────────────────────────────────────────

_STATE_FILE = Path.home() / ".doc-gen-state.json"

_state: dict[str, Any] = {
    "repo_path": None,
    "docs_dir": None,
    "manifest_path": None,
    "last_commit": None,
    "initialized": False,
    "activity": [],
}


def _save_state() -> None:
    data = {k: v for k, v in _state.items() if k != "activity"}
    _STATE_FILE.write_text(json.dumps(data))


def _load_state() -> None:
    if _STATE_FILE.exists():
        try:
            data = json.loads(_STATE_FILE.read_text())
            _state.update(data)
        except Exception:
            pass


def _log(msg: str) -> None:
    entry = {"type": "log", "message": msg}
    _state["activity"].append(entry)
    asyncio.create_task(_manager.broadcast(entry))


async def _cognee_ingest_all() -> None:
    """Ingest all current docs and wiki pages into cognee."""
    if not _COGNEE_AVAILABLE:
        return
    try:
        docs = Path(_state["docs_dir"])
        repo_path = _state["repo_path"]
        manifest_path = _state.get("manifest_path")

        documented = store.list_docs(docs)
        if documented:
            _log(f"Ingesting {len(documented)} file docs into cognee...")
            file_doc_pairs = [(f, store.read_doc(docs, f)) for f in documented]
            await asyncio.get_event_loop().run_in_executor(None, cognee_backend.ingest_docs, file_doc_pairs)
            _log("File docs ingested.")

        if manifest_path and Path(manifest_path).exists():
            pages = manifest_mod.load_manifest(manifest_path)
            wiki_pairs = []
            for page in pages:
                output_key = page.output_path.removesuffix(".md")
                if store.doc_exists(Path(repo_path), output_key):
                    wiki_pairs.append((page.title, store.read_doc(Path(repo_path), output_key)))
            if wiki_pairs:
                _log(f"Ingesting {len(wiki_pairs)} wiki pages into cognee...")
                await asyncio.get_event_loop().run_in_executor(None, cognee_backend.ingest_wiki_pages, wiki_pairs)
                _log("Wiki pages ingested.")
    except Exception as e:
        _log(f"Cognee ingestion skipped: {e}")


# ── WebSocket manager ────────────────────────────────────────────────────────

class _Manager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws) if hasattr(self._connections, "discard") else None
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, data: dict) -> None:
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = _Manager()


# ── Git watcher ──────────────────────────────────────────────────────────────

def _latest_commit(repo_path: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, "log", "-1", "--format=%H"],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


async def _watch_commits() -> None:
    while True:
        await asyncio.sleep(10)
        if not _state["initialized"]:
            continue
        repo_path = _state["repo_path"]
        docs_dir = _state["docs_dir"]
        current = _latest_commit(repo_path)
        if current and current != _state["last_commit"]:
            _log(f"New commit detected: {current[:8]}")
            await _run_update(repo_path, docs_dir, current)
            _state["last_commit"] = current
            await _manager.broadcast({"type": "refresh"})


async def _run_update(repo_path: str, docs_dir: str, sha: str) -> None:
    docs = Path(docs_dir)
    ext = (".go",)
    try:
        diffs = get_commit_diffs(repo_path, sha, ext)
        for d in diffs:
            _log(f"  updating {d.path}")
            if not store.doc_exists(docs, d.path):
                if not d.after.strip():
                    continue
                doc = generator.generate_base_doc(d.path, d.after)
            else:
                existing = store.read_doc(docs, d.path)
                if not d.diff.strip():
                    continue
                doc = generator.update_doc_from_diff(d.path, existing, d.diff)
            store.write_doc(docs, d.path, doc, source_hash=store.content_hash(d.after))

        manifest_path = _state.get("manifest_path")
        if manifest_path and Path(manifest_path).exists():
            pages = manifest_mod.load_manifest(manifest_path)
            changed = get_changed_files(repo_path, sha)
            affected = manifest_mod.find_affected_pages(pages, changed)
            for page in affected:
                _log(f"  rebuilding wiki: {page.title}")
                all_files = list_all_files(repo_path)
                resolved = manifest_mod.resolve_sources(page, all_files)
                file_docs = [(f, store.read_doc(docs, f)) for f in resolved if store.doc_exists(docs, f)]
                if not file_docs:
                    continue
                output_key = page.output_path.removesuffix(".md")
                if store.doc_exists(Path(repo_path), output_key):
                    current_doc = store.read_doc(Path(repo_path), output_key)
                    updated = [(f, store.read_doc(docs, f)) for f in changed if store.doc_exists(docs, f)]
                    if updated:
                        new_doc = wiki_mod.update_wiki_page(page.title, current_doc, updated)
                        source_hash = store.content_hash("\n".join(d for _, d in file_docs))
                        store.write_doc(Path(repo_path), output_key, new_doc, source_hash=source_hash)
        _log(f"Update complete for {sha[:8]}")
        await _cognee_ingest_all()
    except Exception as e:
        _log(f"Error during update: {e}")


# ── API routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (Path(__file__).parent / "templates" / "index.html").read_text()


@app.post("/project/init")
async def init_project(repo_path: str, docs_dir: str = "docs", manifest: str = "wiki.yaml", ext: str = ".go") -> dict:
    docs = Path(docs_dir)
    extensions = tuple(e.strip() if e.strip().startswith(".") else f".{e.strip()}" for e in ext.split(","))

    _state.update({
        "repo_path": repo_path,
        "docs_dir": docs_dir,
        "manifest_path": str(Path(repo_path) / manifest),
        "last_commit": _latest_commit(repo_path),
        "initialized": True,
    })
    _save_state()

    files = list_source_files(repo_path, extensions)
    _log(f"Found {len(files)} source files. Starting init...")

    async def _do_init() -> None:
        for filepath in files:
            if store.doc_exists(docs, filepath):
                content = get_file_content(repo_path, filepath)
                if store.read_hash(docs, filepath) == store.content_hash(content):
                    continue
            _log(f"  init {filepath}")
            content = get_file_content(repo_path, filepath)
            if not content.strip():
                continue
            doc = generator.generate_base_doc(filepath, content)
            store.write_doc(docs, filepath, doc, source_hash=store.content_hash(content))
        _log("Init complete.")
        await _cognee_ingest_all()
        await _manager.broadcast({"type": "refresh"})

    asyncio.create_task(_do_init())
    return {"status": "started", "files": len(files)}


@app.post("/wiki/init")
async def init_wiki(overwrite: bool = False) -> dict:
    if not _state["initialized"]:
        return {"error": "Project not initialized"}
    repo_path = _state["repo_path"]
    docs_dir = _state["docs_dir"]
    manifest_path = _state["manifest_path"]
    docs = Path(docs_dir)

    if not Path(manifest_path).exists():
        return {"error": f"No wiki.yaml found at {manifest_path}"}

    pages = manifest_mod.load_manifest(manifest_path)
    all_files = list_all_files(repo_path)
    _log(f"Generating {len(pages)} wiki pages...")

    async def _do_wiki() -> None:
        for page in pages:
            resolved = manifest_mod.resolve_sources(page, all_files)
            file_docs = []
            for f in resolved:
                if store.doc_exists(docs, f):
                    file_docs.append((f, store.read_doc(docs, f)))
                else:
                    raw = get_file_content(repo_path, f)
                    if raw.strip():
                        file_docs.append((f, raw))
            if not file_docs:
                _log(f"  skip {page.title} (no docs)")
                continue
            source_hash = store.content_hash("\n".join(d for _, d in file_docs))
            output_key = page.output_path.removesuffix(".md")
            if not overwrite and store.doc_exists(Path(repo_path), output_key):
                if store.read_hash(Path(repo_path), output_key) == source_hash:
                    _log(f"  skip {page.title} (unchanged)")
                    continue
            _log(f"  generating {page.title}...")
            doc = wiki_mod.generate_wiki_page(page.title, file_docs, with_diagram=page.mermaid, quickstart=page.quickstart)
            store.write_doc(Path(repo_path), output_key, doc, source_hash=source_hash)
            _log(f"  wrote {page.output_path}")
        _log("Wiki init complete.")
        await _cognee_ingest_all()
        await _manager.broadcast({"type": "refresh"})

    asyncio.create_task(_do_wiki())
    return {"status": "started", "pages": len(pages)}


def _local_search(query: str, top_k: int) -> list[dict]:
    """Keyword search over local wiki pages and file docs."""
    terms = query.lower().split()
    repo_path = _state.get("repo_path")
    docs_dir = _state.get("docs_dir")
    manifest_path = _state.get("manifest_path")
    results = []

    def _score(text: str) -> int:
        low = text.lower()
        return sum(low.count(t) for t in terms)

    # Search wiki pages
    if repo_path and manifest_path and Path(manifest_path).exists():
        pages = manifest_mod.load_manifest(manifest_path)
        wiki_hits = []
        for page in pages:
            output_key = page.output_path.removesuffix(".md")
            if store.doc_exists(Path(repo_path), output_key):
                content = store.read_doc(Path(repo_path), output_key)
                score = _score(content)
                if score > 0:
                    # Return first matching paragraph
                    paras = [p for p in content.split("\n\n") if any(t in p.lower() for t in terms)]
                    excerpt = paras[0] if paras else content[:500]
                    wiki_hits.append((score, f"**{page.title}**\n\n{excerpt}"))
        wiki_hits.sort(key=lambda x: -x[0])
        if wiki_hits:
            results.append({"source": "doc-gen-wiki", "answers": [h for _, h in wiki_hits[:top_k]]})

    # Search file docs
    if docs_dir and Path(docs_dir).exists():
        doc_hits = []
        for filepath in store.list_docs(Path(docs_dir)):
            content = store.read_doc(Path(docs_dir), filepath)
            score = _score(content)
            if score > 0:
                paras = [p for p in content.split("\n\n") if any(t in p.lower() for t in terms)]
                excerpt = paras[0] if paras else content[:500]
                doc_hits.append((score, f"**{filepath}**\n\n{excerpt}"))
        doc_hits.sort(key=lambda x: -x[0])
        if doc_hits:
            results.append({"source": "doc-gen-files", "answers": [h for _, h in doc_hits[:top_k]]})

    return results


@app.get("/search")
async def search(query: str, top_k: int = 5) -> dict:
    repo_path = _state.get("repo_path", ".")
    docs_dir = _state.get("docs_dir", "docs")
    if _COGNEE_AVAILABLE:
        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(None, cognee_backend.search, query, top_k)
            if results:
                return {"results": results}
        except Exception:
            pass

    hits = store.local_search(query, repo_path, docs_dir, top_k)
    if not hits:
        return {"results": []}

    github_info = _get_github_info(repo_path)
    sources = []
    for r in hits:
        title = r["title"]
        is_wiki = not title.startswith("src/")
        if is_wiki:
            sources.append({"label": title, "wiki_path": f"wiki/{title.lower().replace(' ', '-')}.md"})
        elif github_info:
            sources.append({"label": title, "url": f"https://github.com/{github_info['owner']}/{github_info['repo']}/blob/unstable/{title}"})
        else:
            sources.append({"label": title})

    context = "\n\n---\n\n".join(f"# {r['title']}\n{r['excerpt']}" for r in hits)
    try:
        answer = await asyncio.get_event_loop().run_in_executor(
            None,
            generator._call,
            [{"role": "user", "content": (
                f"You are a technical assistant. Answer the question below using only the "
                f"documentation provided. Be concise and direct. Use markdown formatting.\n\n"
                f"Question: {query}\n\n"
                f"Documentation:\n{context}"
            )}],
        )
        return {"results": [{"source": "local", "answers": [answer], "sources": sources}]}
    except Exception:
        return {"results": [{"source": "local", "answers": [r["excerpt"] for r in hits]}]}


@app.get("/version")
async def version() -> dict:
    docs_dir = _state.get("docs_dir")
    if not docs_dir:
        return {}
    return store.read_version(Path(docs_dir)) or {}


@app.get("/wiki")
async def list_wiki() -> dict:
    repo_path = _state.get("repo_path")
    manifest_path = _state.get("manifest_path")
    docs_dir = _state.get("docs_dir")
    result = []

    if manifest_path and Path(manifest_path).exists():
        pages = manifest_mod.load_manifest(manifest_path)
        for page in pages:
            output_key = page.output_path.removesuffix(".md")
            exists = store.doc_exists(Path(repo_path), output_key)
            result.append({"title": page.title, "path": page.output_path, "exists": exists})

    if not any(p["exists"] for p in result) and docs_dir and Path(docs_dir).exists():
        for filepath in sorted(store.list_docs(Path(docs_dir)))[:50]:
            result.append({"title": filepath, "path": filepath + ".md", "exists": True})

    return {"pages": result}


@app.get("/wiki/{page_path:path}")
async def get_wiki_page(page_path: str) -> dict:
    repo_path = _state.get("repo_path")
    docs_dir = _state.get("docs_dir")
    output_key = page_path.removesuffix(".md")

    if repo_path and store.doc_exists(Path(repo_path), output_key):
        return {"content": store.read_doc(Path(repo_path), output_key)}
    if docs_dir and store.doc_exists(Path(docs_dir), output_key):
        return {"content": store.read_doc(Path(docs_dir), output_key)}
    return {"error": "Page not found"}


@app.get("/status")
async def status() -> dict:
    return {
        "initialized": _state["initialized"],
        "repo_path": _state["repo_path"],
        "docs_dir": _state["docs_dir"],
        "last_commit": _state["last_commit"],
    }


@app.get("/activity")
async def activity() -> dict:
    return {"activity": _state["activity"][-100:]}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await _manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _manager.disconnect(ws)


@app.get("/project/github-info")
async def github_info() -> dict:
    repo_path = _state.get("repo_path")
    if not repo_path:
        return {}
    info = _get_github_info(repo_path)
    return info or {}


@app.post("/project/push-wiki")
async def push_wiki() -> dict:
    if not _state["initialized"]:
        return {"error": "Project not initialized"}
    repo_path = _state["repo_path"]
    manifest_path = _state.get("manifest_path")
    if not manifest_path or not Path(manifest_path).exists():
        return {"error": "No wiki.yaml found"}

    pages = manifest_mod.load_manifest(manifest_path)
    if not pages:
        return {"error": "No wiki pages defined"}

    wiki_dir = str(Path(repo_path) / Path(pages[0].output_path).parent)
    _log(f"Pushing wiki to GitHub...")
    err = _push_to_github_wiki(repo_path, wiki_dir)
    if err:
        _log(f"Push failed: {err}")
        return {"error": err}
    info = _get_github_info(repo_path)
    wiki_url = info["wiki_url"] if info else None
    _log(f"Wiki pushed successfully.")
    return {"status": "ok", "wiki_url": wiki_url}


@app.on_event("startup")
async def startup() -> None:
    _load_state()
    asyncio.create_task(_watch_commits())
