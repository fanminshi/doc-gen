# doc-gen

Incremental LLM-powered documentation and wiki generator from git history.
Point it at any git repository and it generates structured, per-file docs —
then keeps them accurate as code evolves using git diffs.

## Architecture

```
[ git repository ]
        │
        │  git tree / unified diffs  (gitpython)
        ▼
┌─────────────────────────────────────────────┐
│              doc-gen ingest layer            │
│                                             │
│  init   → full file content → LLM → .md    │
│  update → git diff only    → LLM → .md     │
│                                             │
│  content_hash frontmatter gates re-gen:     │
│  unchanged files are always skipped         │
└──────────────────────┬──────────────────────┘
                       │  per-file .md docs
                       ▼
┌─────────────────────────────────────────────┐
│           store  (docs/ directory)           │
│  docs/<filepath>.md  +  content_hash         │
└──────────────────────┬──────────────────────┘
                       │  file docs as input
                       ▼
┌─────────────────────────────────────────────┐
│              wiki layer                      │
│                                             │
│  wiki.yaml defines pages + glob sources     │
│  wiki-init  → synthesize file docs → wiki   │
│  wiki-update → patch only affected pages    │
└──────────────────────┬──────────────────────┘
                       │  wiki pages (.md)
                       ▼
┌─────────────────────────────────────────────┐
│           query / search                     │
│                                             │
│  keyword search → top wiki pages            │
│  → LLM synthesizes a single answer          │
│                                             │
│  cognee (optional) → knowledge graph        │
│  Redis  (optional) → session memory         │
└─────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│           web UI  (FastAPI + WebSocket)      │
│  /project/init   → runs init                │
│  /wiki/init      → runs wiki-init           │
│  /search         → query endpoint           │
│  /wiki           → list + read wiki pages   │
│  /ws             → live activity stream     │
└─────────────────────────────────────────────┘
```

### Key components

| File | Role |
|------|------|
| `cli.py` | CLI entry point — `init`, `update`, `replay`, `wiki-init`, `wiki-update`, `query`, `show` |
| `generator.py` | LLM calls — `generate_base_doc` (full file) and `update_doc_from_diff` (diff only) |
| `store.py` | Read/write `.md` docs with `content_hash` frontmatter; `local_search` for keyword retrieval |
| `git.py` | gitpython helpers — list files, get content at ref, get per-commit diffs |
| `manifest.py` | Parse `wiki.yaml`, resolve glob patterns, find affected pages |
| `wiki.py` | LLM calls for wiki page generation and updates |
| `server.py` | FastAPI server — exposes all operations as HTTP endpoints with a WebSocket activity log |
| `cognee_backend.py` | Optional Cognee integration — ingest docs into knowledge graph, semantic search |

### Two-tier memory (Cognee + Redis)

When Cognee is available (`pip install cognee[redis]` + `REDIS_URL` set):

- **Redis** — session memory during a generation run. Tracks in-progress file state so interrupted runs resume rather than restart.
- **Cognee** — permanent knowledge graph. Finished docs are ingested via `cognee.add` + `cognee.cognify`, enabling semantic cross-file recall across sessions.

Without Cognee, `store.local_search` provides keyword search directly over the `.md` files.

## Usage

```sh
# Generate per-file docs (C example: Redis src/)
doc-gen init /path/to/repo --ext .c --ext .h --prefix src/ --docs-dir /path/to/docs

# Update docs for a specific commit
doc-gen update /path/to/repo <sha> --ext .c --ext .h --docs-dir /path/to/docs

# Generate architecture wiki pages
doc-gen wiki-init /path/to/repo --docs-dir /path/to/docs

# Query the wiki
doc-gen query "how does eviction work?" /path/to/repo --docs-dir /path/to/docs

# Launch the web UI
uvicorn doc_gen.server:app --reload
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | LLM for doc generation |
| `REDIS_URL` | Redis session memory (optional) |
| `LLM_API_KEY` | Cognee LLM key (defaults to `OPENAI_API_KEY`) |
| `EMBEDDING_API_KEY` | Cognee embedding key (defaults to `OPENAI_API_KEY`) |
