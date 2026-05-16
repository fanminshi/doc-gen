"""LLM API calls for generating and updating file documentation."""

from __future__ import annotations

import openai

MODEL = "gpt-5.4-mini-2026-03-17"
CHUNK_CHARS = 450_000  # ~250k tokens at ~1.8 chars/token for Go code

MERGE_DOC_PROMPT = """\
You are a technical documentation writer. Below are partial documentation sections \
for different parts of the same file. Merge them into a single, cohesive document.

Remove redundancy, unify the tone, and produce one clean markdown document with headers.

File: {filepath}

{sections}
"""

BASE_DOC_PROMPT = """\
You are a technical documentation writer. Read the source file below and write clear, \
concise documentation for it.

Cover:
- Purpose of the file and what problem it solves
- Key classes, functions, or constants and what they do
- Important design decisions or non-obvious behavior
- How this file relates to the rest of the system (if inferrable)

Be specific and accurate. Do not pad with generic statements.
Write in plain prose with markdown headers. No code fences around the entire doc.

File: {filepath}

```
{content}
```
"""

UPDATE_DOC_PROMPT = """\
You are maintaining technical documentation. A source file has changed. \
Update the existing documentation to reflect the changes shown in the diff below.

Rules:
- Only change sections affected by the diff
- Keep unchanged sections exactly as they are
- If new functions/classes were added, document them
- If something was removed, remove its documentation
- Return the FULL updated documentation (not just the changed parts)

File: {filepath}

Existing documentation:
{existing_doc}

Git diff:
```diff
{diff}
```
"""


def _client() -> openai.OpenAI:
    return openai.OpenAI()


def _chunks(text: str) -> list[str]:
    return [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)]


def _call(messages: list[dict]) -> str:
    return _client().chat.completions.create(
        model=MODEL,
        max_completion_tokens=100000,
        messages=messages,
    ).choices[0].message.content


def generate_base_doc(filepath: str, content: str) -> str:
    """Generate initial documentation for a file from its full content."""
    chunks = _chunks(content)
    if len(chunks) == 1:
        return _call([{"role": "user", "content": BASE_DOC_PROMPT.format(filepath=filepath, content=chunks[0])}])

    sections = []
    for i, chunk in enumerate(chunks, start=1):
        section = _call([{"role": "user", "content": BASE_DOC_PROMPT.format(filepath=f"{filepath} (part {i}/{len(chunks)})", content=chunk)}])
        sections.append(f"## Part {i}\n\n{section}")

    merged = _call([{"role": "user", "content": MERGE_DOC_PROMPT.format(filepath=filepath, sections="\n\n---\n\n".join(sections))}])
    return merged


def update_doc_from_diff(filepath: str, existing_doc: str, diff: str) -> str:
    """Update existing documentation using only the git diff for a commit."""
    chunks = _chunks(diff)
    if len(chunks) == 1:
        return _call([{"role": "user", "content": UPDATE_DOC_PROMPT.format(filepath=filepath, existing_doc=existing_doc, diff=chunks[0])}])

    doc = existing_doc
    for chunk in chunks:
        doc = _call([{"role": "user", "content": UPDATE_DOC_PROMPT.format(filepath=filepath, existing_doc=doc, diff=chunk)}])
    return doc
