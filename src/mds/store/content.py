"""Content store: normalised text derivatives addressed by BLAKE3 digest (DESIGN.md §2.1, §11)."""

import os
import tempfile
from pathlib import Path

import blake3


class ContentError(Exception):
    """A derivative is missing or its bytes no longer match its digest."""


def digest_text(text: str) -> str:
    """BLAKE3 hex digest of `text` encoded as UTF-8."""
    return blake3.blake3(text.encode("utf-8")).hexdigest()


class ContentStore:
    """A directory of derivatives, one file per digest, sharded by the first two hex characters.

    Files are write-once: putting the same text twice stores it once.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ContentError(f"not a BLAKE3 hex digest: {digest!r}")
        return self.root / digest[:2] / digest[2:]

    def put(self, text: str) -> str:
        """Store `text` and return its digest."""
        data = text.encode("utf-8")
        digest = blake3.blake3(data).hexdigest()
        path = self._path(digest)
        if path.exists():
            return digest
        path.parent.mkdir(exist_ok=True)
        # Write to a temporary file and rename so a crash never leaves a partial derivative.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return digest

    def has(self, digest: str) -> bool:
        """Whether a derivative with this digest is stored."""
        return self._path(digest).exists()

    def size(self, digest: str) -> int:
        """Length in bytes of the stored derivative."""
        try:
            return self._path(digest).stat().st_size
        except FileNotFoundError:
            raise ContentError(f"no derivative stored for {digest}") from None

    def get_bytes(self, digest: str) -> bytes:
        """The stored UTF-8 bytes for `digest`, verified against the digest."""
        path = self._path(digest)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise ContentError(f"no derivative stored for {digest}") from None
        if blake3.blake3(data).hexdigest() != digest:
            raise ContentError(f"derivative {digest} is corrupt")
        return data

    def get(self, digest: str) -> str:
        """The stored text for `digest`."""
        return self.get_bytes(digest).decode("utf-8")

    def slice(self, digest: str, byte_offset: int, byte_length: int) -> str:
        """The text in a UTF-8 byte range of a derivative, as an atom version points at it."""
        data = self.get_bytes(digest)
        end = byte_offset + byte_length
        if byte_offset < 0 or byte_length < 0 or end > len(data):
            raise ContentError(
                f"byte range {byte_offset}+{byte_length} is outside derivative {digest} "
                f"({len(data)} bytes)"
            )
        try:
            return data[byte_offset:end].decode("utf-8")
        except UnicodeDecodeError:
            raise ContentError(
                f"byte range {byte_offset}+{byte_length} splits a UTF-8 character"
            ) from None
