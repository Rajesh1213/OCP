"""File-system workspace indexer."""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

from ocp_server.embedder import Embedder
from ocp_server.models import Chunk, ChunkSource, SourceRange, make_chunk_id
from ocp_server.storage.base import BaseStore

_TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
    ".rb", ".cpp", ".c", ".h", ".cs", ".swift", ".md", ".txt", ".yaml",
    ".yml", ".json", ".toml", ".html", ".css", ".sh", ".sql",
}
_MAX_CHUNK_BYTES = 4096


async def index_workspace(
    store: BaseStore,
    embedder: Embedder,
    workspace_id: str,
    root_uri: str,
    paths: list[str] | None,
) -> dict:
    root = root_uri.removeprefix("file://")
    root_path = Path(root)

    if not root_path.exists():
        return {"indexed": 0, "skipped": 0, "duration_ms": 0}

    import time
    t0 = time.monotonic()

    if paths:
        targets = [root_path / p.lstrip("/") for p in paths]
    else:
        targets = [root_path]

    indexed = 0
    skipped = 0

    for target in targets:
        if target.is_file():
            files = [target]
        else:
            files = [p for p in target.rglob("*") if p.is_file()]

        for file_path in files:
            if file_path.suffix.lower() not in _TEXT_EXTENSIONS:
                skipped += 1
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                skipped += 1
                continue

            chunks = _chunk_file(workspace_id, file_path, root_path, text)
            for chunk in chunks:
                embedding = await embedder.embed(chunk.content)
                await store.upsert_chunk(chunk, embedding)
                indexed += 1

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {"indexed": indexed, "skipped": skipped, "duration_ms": duration_ms}


def _chunk_file(workspace_id: str, file_path: Path, root: Path, text: str) -> list[Chunk]:
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        rel = file_path
    uri = f"file://{rel}"

    lines = text.splitlines(keepends=True)
    chunks: list[Chunk] = []
    start_line = 0

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

        start_byte = sum(len(l.encode()) for l in lines[:start_line])
        end_byte = start_byte + len(buf.encode())

        ext = file_path.suffix.lstrip(".")
        lang_map = {"py": "python", "ts": "typescript", "tsx": "typescript",
                    "js": "javascript", "jsx": "javascript", "go": "go",
                    "rs": "rust", "java": "java", "rb": "ruby", "md": "markdown"}
        language = lang_map.get(ext)

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
