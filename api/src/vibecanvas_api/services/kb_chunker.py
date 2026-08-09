"""Token-based recursive chunking for knowledge-base content.

Strategy:
1. Split per ParsedSegment first (never cross boundaries — preserves page/section).
2. Within each segment, try separators in order ["\n\n", "\n", "。", ".", " "].
3. If still > chunk_size after the lowest separator, hard-split at token boundary.
4. Adjacent chunks share `overlap` tokens of context.
5. Propagate ParsedSegment.metadata to each emitted chunk; add chunk_index
   (global monotonic counter across all segments in one split() call).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import tiktoken

from .parsers.base import ParsedSegment


@dataclass
class Chunk:
    text: str
    metadata: dict


_SEPARATORS = ["\n\n", "\n", "。", ".", " "]


class RecursiveTokenChunker:
    def __init__(
        self,
        model: str = "text-embedding-3-small",
        chunk_size: int = 500,
        overlap: int = 100,
    ):
        try:
            self.encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        self.size = chunk_size
        self.overlap = overlap

    def split(self, segments: Iterable[ParsedSegment]) -> list[Chunk]:
        out: list[Chunk] = []
        idx_counter = 0
        for seg in segments:
            pieces = self._split_segment(seg.text)
            for body in pieces:
                meta = dict(seg.metadata)
                meta["chunk_index"] = idx_counter
                out.append(Chunk(text=body, metadata=meta))
                idx_counter += 1
        return out

    def _split_segment(self, text: str) -> list[str]:
        token_count = len(self.encoder.encode(text))
        if token_count <= self.size:
            return [text]
        # Try separators in order
        for sep in _SEPARATORS:
            if sep not in text:
                continue
            parts = self._merge_by_size(text.split(sep), sep)
            if all(len(self.encoder.encode(p)) <= self.size for p in parts):
                return self._add_overlap(parts)
        # Hard token-split fallback
        tokens = self.encoder.encode(text)
        parts = []
        step = self.size - self.overlap
        for i in range(0, len(tokens), step):
            parts.append(self.encoder.decode(tokens[i:i + self.size]))
        return parts

    def _merge_by_size(self, parts: list[str], sep: str) -> list[str]:
        out: list[str] = []
        buf = ""
        for p in parts:
            candidate = (buf + sep + p) if buf else p
            if len(self.encoder.encode(candidate)) <= self.size:
                buf = candidate
            else:
                if buf:
                    out.append(buf)
                buf = p
        if buf:
            out.append(buf)
        return out

    def _add_overlap(self, parts: list[str]) -> list[str]:
        if self.overlap <= 0 or len(parts) <= 1:
            return parts
        out = [parts[0]]
        for i in range(1, len(parts)):
            prev_tokens = self.encoder.encode(parts[i - 1])
            tail = self.encoder.decode(prev_tokens[-self.overlap:])
            out.append(tail + " " + parts[i])
        return out
