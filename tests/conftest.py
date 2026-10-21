import hashlib
import re
from collections.abc import Sequence

import pytest


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder standing in for a real model."""

    def __init__(self, dims: int = 64) -> None:
        self.dims = dims
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for text in texts:
            vector = [0.0] * self.dims
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                h = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=4).digest(), "big")
                vector[h % self.dims] += 1.0
            out.append(vector)
        return out


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
