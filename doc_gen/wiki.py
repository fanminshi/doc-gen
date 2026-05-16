"""LLM generation for wiki pages."""

from __future__ import annotations

from doc_gen.generator import CHUNK_CHARS, _client

WIKI_MODEL = "gpt-5.5-2026-04-23"

SYSTEM_PROMPT = """\
You are a senior technical writer at a top-tier software company. \
You write documentation that engineers actually want to read — clear, well-structured, \
and immediately useful. You follow Google Developer Documentation Style Guide principles: \
active voice, present tense, second person ("you"), short sentences, and concrete examples. \
Your output is always production-quality markdown, never a rough draft."""

WIKI_GEN_PROMPT = """\
Create a professional wiki page titled "{title}" based on the source file documentation below.

## Required Structure

Start with a 2-3 sentence summary paragraph (no heading) that answers: what is this, \
why does it exist, and who uses it.

Then include ALL of the following sections:

### Table of Contents
A markdown list of anchor links to each major section below.

### Overview
- What problem this component solves
- Where it fits in the overall system
- Key design principles or constraints
{diagram_instruction}

### Key Components
A table with columns: | Component | Type | Responsibility |
Follow with a short paragraph on how the components interact.

### How It Works
Step-by-step description of the main workflow or data flow. Use numbered lists.
Include a code example (in a fenced code block with language tag) if one can be inferred.

### Configuration & Extension Points
Any flags, environment variables, interfaces, or hooks that users or developers can configure.
Use a table where there are multiple options: | Name | Type | Default | Description |

### Error Handling & Edge Cases
Non-obvious failure modes, retry behavior, or important constraints. Use a callout:
> **Note:** ...

### Source Files
| File | Responsibility |
|------|----------------|
(one row per file)

## Style Rules
- Use `code formatting` for all file names, function names, types, and commands
- Use **bold** for key terms on first use
- Use > blockquotes for notes, warnings, and tips
- Every section must have at least 2 sentences — no stubs
- Do not use vague phrases like "various", "several", "some", "handles X"
- Write in second person ("you") where addressing the reader

Source file documentation:
{file_docs}
"""

QUICKSTART_PROMPT = """\
Write a professional Quick Start guide inferred entirely from the source code below. \
Do not use any external knowledge — derive all commands, flags, and workflows from \
the code itself (CLI definitions, test data, main entry points).

## Required Structure

Start with a single bold sentence: **In this guide, you will [specific outcome].**

Then include ALL of the following sections in order:

### Prerequisites
A table: | Requirement | Version | Notes |
Infer requirements from imports, build constraints, and CLI flag descriptions.

### Installation
Numbered steps with fenced shell code blocks. Include the expected success output as a \
commented line after each command where inferable:
```sh
some-command  # ✓ outputs: ...
```

### Create Your First [Thing]
Numbered steps. Every step must:
1. State what you're doing in one sentence
2. Show the exact command in a fenced `sh` code block
3. Briefly explain what the command does

### Verify It Works
How to confirm the setup worked. Include a command and expected output.

### Common Options
A table of the most useful flags: | Flag | Default | Description |

### Troubleshooting
A short table of common errors and fixes: | Error | Cause | Fix |
Only include errors you can confidently infer from the code.

### Next Steps
A bulleted list of 3-5 capabilities to explore next, inferred from the codebase.

## Style Rules
- Use `code formatting` for all commands, flags, and file names
- Use **bold** for key terms on first use
- Every step must have a runnable command — omit the step if you cannot infer one
- Write in second person ("you")
- No placeholder text — be specific or omit

Source code:
{file_docs}
"""

WIKI_UPDATE_PROMPT = """\
Update the wiki page below to reflect changes in the source files. \
Follow the same professional style and structure as the existing page.

Rules:
- Only revise sections directly affected by the changed files
- Keep all unaffected sections exactly as they are
- Maintain the same table of contents, heading structure, and formatting style
- Return the FULL updated page — not just the changed parts
- Do not add a changelog or "Updated" section

Wiki page title: {title}

Current wiki page:
{current_doc}

Updated documentation for changed source files:
{file_docs}
"""

MERGE_PROMPT = """\
Merge these two partial drafts for the wiki page "{title}" into one polished, \
professional document. Follow Google Developer Documentation Style Guide.

Requirements:
- Single coherent narrative — not two sections concatenated
- Remove all redundancy
- Unify tone (second person, present tense, active voice)
- Preserve all tables, code blocks, and callouts
- Produce a complete page with Table of Contents

Part 1:
{part1}

Part 2:
{part2}
"""

DIAGRAM_INSTRUCTION = """\

### Architecture Diagram
Include a Mermaid diagram that shows the key components and their relationships.
Use `graph TD` for dependency/component diagrams or `sequenceDiagram` for flows.
Label all nodes clearly. Keep it focused — show the 5-8 most important relationships only.
"""


def _wiki_call(messages: list[dict]) -> str:
    response = _client().chat.completions.create(
        model=WIKI_MODEL,
        max_completion_tokens=100000,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise RuntimeError(f"Model {WIKI_MODEL} returned empty content. Check your API key and model name.")
    return content


def _format_docs(file_docs: list[tuple[str, str]]) -> str:
    return "\n\n---\n\n".join(f"### {path}\n\n{doc}" for path, doc in file_docs)


def generate_wiki_page(title: str, file_docs: list[tuple[str, str]], with_diagram: bool = False, quickstart: bool = False) -> str:
    docs_text = _format_docs(file_docs)

    if quickstart:
        return _wiki_call([{"role": "user", "content": QUICKSTART_PROMPT.format(file_docs=docs_text)}])

    diagram_instruction = DIAGRAM_INSTRUCTION if with_diagram else ""

    if len(docs_text) <= CHUNK_CHARS:
        return _wiki_call([{"role": "user", "content": WIKI_GEN_PROMPT.format(
            title=title,
            diagram_instruction=diagram_instruction,
            file_docs=docs_text,
        )}])

    mid = len(file_docs) // 2
    part1 = _wiki_call([{"role": "user", "content": WIKI_GEN_PROMPT.format(
        title=f"{title} (part 1)",
        diagram_instruction="",
        file_docs=_format_docs(file_docs[:mid]),
    )}])
    part2 = _wiki_call([{"role": "user", "content": WIKI_GEN_PROMPT.format(
        title=f"{title} (part 2)",
        diagram_instruction=diagram_instruction,
        file_docs=_format_docs(file_docs[mid:]),
    )}])
    return _wiki_call([{"role": "user", "content": MERGE_PROMPT.format(title=title, part1=part1, part2=part2)}])


def update_wiki_page(title: str, current_doc: str, updated_file_docs: list[tuple[str, str]]) -> str:
    return _wiki_call([{"role": "user", "content": WIKI_UPDATE_PROMPT.format(
        title=title,
        current_doc=current_doc,
        file_docs=_format_docs(updated_file_docs),
    )}])
