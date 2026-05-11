"""File-system workspace indexer.

Changes:
  §6.1 trigger 3 — detect hash-mismatch on reindex: stale old chunks and return
                   their IDs so the caller can emit chunk.invalidated.
  §7.2            — accepts optional progress_cb for index.progress events.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Awaitable, Callable

from ocp_server.embedder import EmbedderProtocol as Embedder
from ocp_server.models import Chunk, ChunkSource, SourceRange, make_chunk_id
from ocp_server.storage.base import BaseStore

_TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
    ".rb", ".cpp", ".c", ".h", ".cs", ".swift", ".md", ".txt", ".yaml",
    ".yml", ".json", ".toml", ".html", ".css", ".sh", ".sql",
}
_MAX_CHUNK_BYTES = 4096

# Type alias: receives (progress 0..1) and returns None
ProgressCallback = Callable[[float], Awaitable[None]]


async def index_workspace(
    store: BaseStore,
    embedder: Embedder,
    workspace_id: str,
    root_uri: str,
    paths: list[str] | None,
    progress_cb: ProgressCallback | None = None,
) -> tuple[dict, list[str]]:
    """Index files in the workspace.

    Returns (result_dict, stale_chunk_ids).
    stale_chunk_ids contains IDs of previously-active chunks whose content_hash
    changed during this reindex (§6.1 trigger 3).
    """
    root = root_uri.removeprefix("file://")
    root_path = Path(root)

    if not root_path.exists():
        return {"indexed": 0, "skipped": 0, "duration_ms": 0}, []

    t0 = time.monotonic()

    if paths:
        targets = [root_path / p.lstrip("/") for p in paths]
    else:
        targets = [root_path]

    # Collect all candidate files first so we can report progress
    all_files: list[Path] = []
    for target in targets:
        if target.is_file():
            all_files.append(target)
        else:
            all_files.extend(p for p in target.rglob("*") if p.is_file())

    total = len(all_files)
    indexed = 0
    skipped = 0
    stale_ids: list[str] = []

    for i, file_path in enumerate(all_files):
        if file_path.suffix.lower() not in _TEXT_EXTENSIONS:
            skipped += 1
        else:
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                skipped += 1
            else:
                uri = f"file://{file_path.resolve()}"

                # §6.1 trigger 3: snapshot active chunk IDs before reindexing this file
                prior_ids = await store.get_active_chunk_ids_for_uri(workspace_id, uri)
                prior_set = set(prior_ids)

                new_chunks = _chunk_file(workspace_id, file_path, root_path, text)
                new_ids: set[str] = set()
                for chunk in new_chunks:
                    embedding = await embedder.embed(chunk.content)
                    await store.upsert_chunk(chunk, embedding)
                    new_ids.add(chunk.id)
                    indexed += 1

                # Any prior active chunk not in the new set has a changed hash
                outdated = prior_set - new_ids
                if outdated:
                    await store.mark_chunks_stale(list(outdated))
                    stale_ids.extend(outdated)

        # Emit progress (§7.2 index.progress)
        if progress_cb and total > 0:
            await progress_cb((i + 1) / total)

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {"indexed": indexed, "skipped": skipped, "duration_ms": duration_ms}, stale_ids


def _chunk_file(workspace_id: str, file_path: Path, root: Path, text: str) -> list[Chunk]:
    # R1: absolute resolved URI — matches invalidation LIKE-patterns (includes symlink resolution)
    uri = f"file://{file_path.resolve()}"

    lines = text.splitlines(keepends=True)
    chunks: list[Chunk] = []
    start_line = 0
    ext = file_path.suffix.lstrip(".")
    lang_map = {
        "py": "python", "ts": "typescript", "tsx": "typescript",
        "js": "javascript", "jsx": "javascript", "go": "go",
        "rs": "rust", "java": "java", "rb": "ruby", "md": "markdown",
    }
    language = lang_map.get(ext)

    while start_line < len(lines):
        buf = ""
        end_line = start_line
        while end_line < len(lines) and len((buf + lines[end_line]).encode()) < _MAX_CHUNK_BYTES:
            buf += lines[end_line]
            end_line += 1
        if not buf.strip():
            start_line = end_line + 1
            continue

        content_hash = hashlib.sha256(buf.encode()).hexdigest()
        range_repr = f"{start_line}:{end_line}"
        chunk_id = make_chunk_id(workspace_id, uri, range_repr, content_hash)

        start_byte = sum(len(ln.encode()) for ln in lines[:start_line])
        end_byte = start_byte + len(buf.encode())

        chunks.append(Chunk(
            id=chunk_id,
            workspace_id=workspace_id,
            source=ChunkSource(
                uri=uri,
                range=SourceRange(
                    start_line=start_line,
                    end_line=end_line - 1,
                    start_byte=start_byte,
                    end_byte=end_byte,
                ),
                content_hash=content_hash,
            ),
            kind="section",
            language=language,
            content=buf,
        ))
        start_line = end_line

    return chunks
