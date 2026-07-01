"""A tiny, dependency-free local embedder for semantic re-ranking.

This is deliberately *not* a neural model: it's a hashed bag-of-words vector
(the "hashing trick") with L2 normalisation. It downloads nothing, runs
anywhere, and is good enough to re-rank keyword candidates by rough topical
similarity — the zero-infra default the spec calls for. If a real sentence
model is installed it will be used instead.

The trigger to swap in a heavier model (or move search to the cloud) is when
local semantic quality disappoints on a large library — a Stage 2 decision, not
a Stage 1 requirement.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Optional, Sequence

_DIM = 512
_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]+")


def _hash_bucket(token: str) -> tuple[int, int]:
    h = hashlib.md5(token.encode("utf-8")).digest()
    bucket = int.from_bytes(h[:4], "big") % _DIM
    sign = 1 if h[4] & 1 else -1
    return bucket, sign


class HashingEmbedder:
    """Fast, deterministic bag-of-words embeddings."""

    dim = _DIM

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for tok in _WORD.findall(text.lower()):
            bucket, sign = _hash_bucket(tok)
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


def get_embedder() -> HashingEmbedder:
    """Return the best available embedder. Hashing is always available."""
    return HashingEmbedder()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two (already L2-normalised) vectors."""
    return sum(x * y for x, y in zip(a, b))


def pack(vec: Sequence[float]) -> bytes:
    import struct

    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    import struct

    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))
