from pathlib import Path

from zero_strike.agent.embeddings import EmbeddingCache, cosine


def test_cosine_orthogonal():
    assert cosine([1, 0, 0], [0, 1, 0]) == 0.0


def test_cosine_parallel():
    assert abs(cosine([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-9


def test_cosine_empty_returns_zero():
    assert cosine([], [1, 2, 3]) == 0.0
    assert cosine([0, 0, 0], [1, 2, 3]) == 0.0


def test_embedding_cache_persists(tmp_path: Path):
    cache = EmbeddingCache(path=tmp_path / "e.sqlite")
    cache.put("k1", [0.1, 0.2, 0.3])
    cache.close()
    cache2 = EmbeddingCache(path=tmp_path / "e.sqlite")
    assert cache2.get("k1") == [0.1, 0.2, 0.3]
