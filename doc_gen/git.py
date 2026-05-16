"""Git helpers: file listing, full content, and per-commit diffs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import git


@dataclass
class FileDiff:
    path: str
    before: str  # file content before the commit (empty string if new file)
    after: str   # file content after the commit (empty string if deleted)
    diff: str    # unified diff text


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    diffs: list[FileDiff] = field(default_factory=list)


def open_repo(repo_path: str) -> git.Repo:
    return git.Repo(repo_path)


def list_source_files(repo_path: str, extensions: tuple[str, ...] = (".py",), ref: str = "HEAD") -> list[str]:
    """Return repo-relative paths of all tracked source files at a given ref."""
    repo = open_repo(repo_path)
    return [
        item.path
        for item in repo.commit(ref).tree.traverse()
        if isinstance(item, git.Blob) and item.path.endswith(extensions)
    ]


def get_file_content(repo_path: str, filepath: str, ref: str = "HEAD") -> str:
    """Return file content at a given git ref."""
    repo = open_repo(repo_path)
    try:
        return repo.commit(ref).tree[filepath].data_stream.read().decode("utf-8", errors="replace")
    except KeyError:
        return ""


def get_commits(repo_path: str, max_count: int | None = None) -> list[git.Commit]:
    """Return commits in reverse chronological order (newest first)."""
    repo = open_repo(repo_path)
    kwargs = {}
    if max_count is not None:
        kwargs["max_count"] = max_count
    return list(repo.iter_commits("HEAD", **kwargs))


def get_file_commits(repo_path: str, filepath: str, max_count: int | None = None) -> list[git.Commit]:
    """Return commits that touched a specific file."""
    repo = open_repo(repo_path)
    kwargs = {"paths": filepath}
    if max_count is not None:
        kwargs["max_count"] = max_count
    return list(repo.iter_commits("HEAD", **kwargs))


def list_all_files(repo_path: str, ref: str = "HEAD") -> list[str]:
    """Return repo-relative paths of ALL tracked files at a given ref."""
    repo = open_repo(repo_path)
    return [
        item.path
        for item in repo.commit(ref).tree.traverse()
        if isinstance(item, git.Blob)
    ]


def get_changed_files(repo_path: str, sha: str) -> list[str]:
    """Return repo-relative paths of all files changed in commit SHA."""
    repo = open_repo(repo_path)
    commit = repo.commit(sha)
    parent = commit.parents[0] if commit.parents else None
    raw_diffs = parent.diff(commit) if parent else commit.diff(git.NULL_TREE)
    return [d.b_path or d.a_path for d in raw_diffs]


def get_file_diff_between_refs(repo_path: str, filepath: str, from_ref: str, to_ref: str = "HEAD") -> FileDiff | None:
    """Return a composite diff for a single file between two refs."""
    repo = open_repo(repo_path)
    from_commit = repo.commit(from_ref)
    to_commit = repo.commit(to_ref)

    diffs = from_commit.diff(to_commit, paths=[filepath], create_patch=True)
    if not diffs:
        return None

    d = diffs[0]
    before = d.a_blob.data_stream.read().decode("utf-8", errors="replace") if d.a_blob else ""
    after = d.b_blob.data_stream.read().decode("utf-8", errors="replace") if d.b_blob else ""
    diff_text = d.diff.decode("utf-8", errors="replace") if isinstance(d.diff, bytes) else (d.diff or "")
    return FileDiff(path=filepath, before=before, after=after, diff=diff_text)


def get_commit_diffs(repo_path: str, sha: str, extensions: tuple[str, ...] = (".py",)) -> list[FileDiff]:
    """Return per-file diffs for a given commit."""
    repo = open_repo(repo_path)
    commit = repo.commit(sha)
    parent = commit.parents[0] if commit.parents else None

    diffs = []
    raw_diffs = parent.diff(commit, create_patch=True) if parent else commit.diff(git.NULL_TREE, create_patch=True)

    for d in raw_diffs:
        path = d.b_path or d.a_path
        if not path.endswith(extensions):
            continue

        before = d.a_blob.data_stream.read().decode("utf-8", errors="replace") if d.a_blob else ""
        after = d.b_blob.data_stream.read().decode("utf-8", errors="replace") if d.b_blob else ""
        diff_text = d.diff.decode("utf-8", errors="replace") if isinstance(d.diff, bytes) else (d.diff or "")

        diffs.append(FileDiff(path=path, before=before, after=after, diff=diff_text))

    return diffs
