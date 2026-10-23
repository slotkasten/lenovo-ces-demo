"""Embedding tests — deterministic, normalised, fixed-dimension."""
import math
from app.embeddings import HashingEmbedder, to_pgvector, tokenize


def test_deterministic_and_normalised():
    e = HashingEmbedder(dim=384)
    a = e.embed("wire transfer fees")
    b = e.embed("wire transfer fees")
    assert a == b
    assert len(a) == 384
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-6)


def test_similar_texts_closer_than_unrelated():
    e = HashingEmbedder(dim=512)
    q = e.embed("how do I report a lost or stolen card")
    near = e.embed("report a lost or stolen debit card immediately")
    far = e.embed("summit savings annual percentage yield interest")
    dot = lambda x, y: sum(a * b for a, b in zip(x, y))
    assert dot(q, near) > dot(q, far)


def test_empty_text_is_safe():
    e = HashingEmbedder(dim=64)
    v = e.embed("")
    assert len(v) == 64
    assert any(v)  # not all zero (cosine distance would be undefined)


def test_to_pgvector_format():
    s = to_pgvector([0.1, -0.2, 0.3])
    assert s.startswith("[") and s.endswith("]")
    assert s.count(",") == 2


def test_tokenize_adds_bigrams():
    toks = tokenize("wire transfer fees")
    assert "wire" in toks and "transfer_fees" in toks
