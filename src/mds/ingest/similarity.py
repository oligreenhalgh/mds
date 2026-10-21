"""Word shingles and MinHash for re-anchoring and near-duplicates (DESIGN §2.2, §9)."""

import re
from dataclasses import dataclass

from datasketch import MinHash

SHINGLE_WORDS = 3
NUM_PERM = 128
# Fixed so signatures are identical across runs and machines.
SEED = 1

_WORD = re.compile(r"\w+")


def shingles(text: str, k: int = SHINGLE_WORDS) -> frozenset[str]:
    """Lower-cased word k-grams. Texts shorter than k words give one shingle of all their words."""
    words = _WORD.findall(text.lower())
    if not words:
        return frozenset()
    if len(words) < k:
        return frozenset({" ".join(words)})
    return frozenset(" ".join(words[i : i + k]) for i in range(len(words) - k + 1))


@dataclass(frozen=True, slots=True)
class Signature:
    """A MinHash signature with the size of the shingle set it came from."""

    minhash: MinHash
    size: int

    def jaccard(self, other: "Signature") -> float:
        """Estimated Jaccard similarity; 0 when either text has no words."""
        if not self.size or not other.size:
            return 0.0
        return float(self.minhash.jaccard(other.minhash))

    def containment_in(self, other: "Signature") -> float:
        """Estimated share of this text's shingles that also occur in `other`.

        Derived from the Jaccard estimate and the exact set sizes:
        |A ∩ B| = J (|A| + |B|) / (1 + J).
        """
        j = self.jaccard(other)
        if j == 0.0:
            return 0.0
        overlap = j * (self.size + other.size) / (1.0 + j)
        return min(overlap / self.size, 1.0)


def signature(text: str) -> Signature:
    """MinHash signature of a text's shingles."""
    grams = shingles(text)
    mh = MinHash(num_perm=NUM_PERM, seed=SEED)
    for gram in grams:
        mh.update(gram.encode("utf-8"))
    return Signature(mh, len(grams))
