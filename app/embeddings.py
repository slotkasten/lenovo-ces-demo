"""Text embeddings for the pgvector knowledge base.

Two providers:

* ``hashing`` — a dependency-free "hashing trick" bag-of-words embedding
  (unigrams + bigrams, sub-linear TF, L2-normalised). Quality is lexical rather
  than semantic, but it needs no model, no GPU and ~0 ms per query, which keeps
  the demo deployable on the smallest clusters.
* ``ollama`` — calls an Ollama embedding model (default ``all-minilm``, 384-d).

Both produce vectors of ``settings.embed_dim`` so the pgvector column shape is
fixed regardless of provider.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Protocol

import httpx

from .config import settings

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    """a an and are as at be by for from has have how i in is it its my of on or
    that the this to was what when where which who will with you your do does
    can me our we us about into than then there these those""".split()
)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    tokens = [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]
    bigrams = [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    return tokens + bigrams


class HashingEmbedder:
    name = "hashing"

    def __init__(self, dim: int | None = None):
        self.dim = dim or settings.embed_dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        counts: dict[str, int] = {}
        for tok in tokenize(text):
            counts[tok] = counts.get(tok, 0) + 1
        for tok, count in counts.items():
            digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            weight = 0.5 if "_" in tok else 1.0  # bigrams count a little less
            vec[idx] += sign * weight * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[0] = 1.0  # avoid a zero vector (undefined cosine distance)
            return vec
        return [v / norm for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class OllamaEmbedder:
    name = "ollama"

    def __init__(self, url: str | None = None, model: str | None = None, dim: int | None = None):
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.ollama_embed_model
        self.dim = dim or settings.embed_dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = httpx.post(
            f"{self.url}/api/embed",
            json={"model": self.model, "input": texts},
            timeout=settings.llm_timeout_s,
        )
        resp.raise_for_status()
        vectors = resp.json()["embeddings"]
        for v in vectors:
            if len(v) != self.dim:
                raise ValueError(
                    f"Embedding model {self.model} returned {len(v)} dimensions but EMBED_DIM={self.dim}"
                )
        return vectors

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]


def get_embedder() -> Embedder:
    if settings.embed_provider_resolved == "ollama":
        return OllamaEmbedder()
    return HashingEmbedder()


def to_pgvector(vec: list[float]) -> str:
    """Serialise a vector as a pgvector literal, e.g. '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
