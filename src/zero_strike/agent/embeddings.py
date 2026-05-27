"""Embedding provider + SQLite cache.

Tries Voyage AI first (Anthropic-recommended; tight integration with Claude).
Falls back to OpenAI's text-embedding-3-small if VOYAGE_API_KEY is absent and
OPENAI_API_KEY is set. If neither is configured, the embed() call returns
None — callers should degrade to the keyword matcher.

Cache: ~/.zero-strike/embeddings.sqlite, keyed by (model, content_hash).
Embeddings are deterministic per (model, text), so SHA-256 is a safe key.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable

import httpx

from ..config import settings


_VOYAGE_MODEL = "voyage-3"
_OPENAI_MODEL = "text-embedding-3-small"


def _provider() -> tuple[str, str] | None:
    if os.getenv("VOYAGE_API_KEY"):
        return ("voyage", _VOYAGE_MODEL)
    if os.getenv("OPENAI_API_KEY"):
        return ("openai", _OPENAI_MODEL)
    return None


def _hash(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


class EmbeddingCache:
    def __init__(self, path: Path | None = None):
        self.path = path or (settings.data_dir / "embeddings.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vec TEXT NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str) -> list[float] | None:
        row = self._conn.execute("SELECT vec FROM embeddings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, vec: list[float]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO embeddings (key, vec) VALUES (?, ?)",
            (key, json.dumps(vec)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


_cache_singleton: EmbeddingCache | None = None


def _cache() -> EmbeddingCache:
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = EmbeddingCache()
    return _cache_singleton


def _embed_voyage(texts: list[str], model: str) -> list[list[float]]:
    key = os.environ["VOYAGE_API_KEY"]
    with httpx.Client(timeout=30) as c:
        r = c.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json={"input": texts, "model": model, "input_type": "document"},
        )
        r.raise_for_status()
        data = r.json()["data"]
        return [d["embedding"] for d in data]


def _embed_openai(texts: list[str], model: str) -> list[list[float]]:
    key = os.environ["OPENAI_API_KEY"]
    with httpx.Client(timeout=30) as c:
        r = c.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json={"input": texts, "model": model},
        )
        r.raise_for_status()
        data = r.json()["data"]
        return [d["embedding"] for d in data]


def embed(texts: Iterable[str]) -> list[list[float]] | None:
    """Embed a batch of texts; returns None if no provider is configured.

    Cache-first: only un-cached texts hit the network.
    """
    prov = _provider()
    if prov is None:
        return None
    provider, model = prov
    texts_list = list(texts)
    cache = _cache()
    keys = [_hash(model, t) for t in texts_list]
    results: list[list[float] | None] = [cache.get(k) for k in keys]
    missing = [(i, t) for i, (t, r) in enumerate(zip(texts_list, results)) if r is None]
    if missing:
        idxs, texts_to_fetch = zip(*missing)
        if provider == "voyage":
            fresh = _embed_voyage(list(texts_to_fetch), model)
        else:
            fresh = _embed_openai(list(texts_to_fetch), model)
        for idx, vec in zip(idxs, fresh):
            results[idx] = vec
            cache.put(keys[idx], vec)
    # results are now all populated
    return [r for r in results if r is not None]


def cosine(a: list[float], b: list[float]) -> float:
    import math

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
