from pathlib import Path

import blake3
import pytest

from mds.store import ContentError, ContentStore, digest_text


@pytest.fixture
def content(tmp_path: Path) -> ContentStore:
    return ContentStore(tmp_path / "content")


def test_round_trip(content: ContentStore) -> None:
    text = "Section 4.2 — retention: six years. Ünïcödé ✓\n"
    digest = content.put(text)
    assert digest == blake3.blake3(text.encode("utf-8")).hexdigest() == digest_text(text)
    assert content.has(digest)
    assert content.get(digest) == text
    assert content.size(digest) == len(text.encode("utf-8"))


def test_empty_text_round_trip(content: ContentStore) -> None:
    assert content.get(content.put("")) == ""


def test_put_is_idempotent_and_sharded(content: ContentStore, tmp_path: Path) -> None:
    d1 = content.put("same")
    d2 = content.put("same")
    assert d1 == d2
    files = [p for p in (tmp_path / "content").rglob("*") if p.is_file()]
    assert files == [tmp_path / "content" / d1[:2] / d1[2:]]


def test_no_temporary_files_left(content: ContentStore, tmp_path: Path) -> None:
    content.put("a")
    content.put("b")
    assert not [p for p in (tmp_path / "content").rglob(".tmp-*")]


def test_missing_digest(content: ContentStore) -> None:
    missing = digest_text("never stored")
    assert not content.has(missing)
    with pytest.raises(ContentError, match="no derivative stored"):
        content.get(missing)
    with pytest.raises(ContentError, match="no derivative stored"):
        content.size(missing)


@pytest.mark.parametrize("bad", ["", "abc", "AB" * 32, "../" + "a" * 61])
def test_malformed_digest_rejected(content: ContentStore, bad: str) -> None:
    with pytest.raises(ContentError, match="not a BLAKE3 hex digest"):
        content.has(bad)


def test_corruption_detected(content: ContentStore, tmp_path: Path) -> None:
    digest = content.put("original")
    (tmp_path / "content" / digest[:2] / digest[2:]).write_bytes(b"tampered")
    with pytest.raises(ContentError, match="corrupt"):
        content.get(digest)


def test_slice_uses_utf8_byte_offsets(content: ContentStore) -> None:
    text = "é4.2 Retention"
    digest = content.put(text)
    # "é" is two bytes, so "4.2" starts at byte 2.
    assert content.slice(digest, 2, 3) == "4.2"
    assert content.slice(digest, 0, 2) == "é"


def test_slice_out_of_range(content: ContentStore) -> None:
    digest = content.put("short")
    with pytest.raises(ContentError, match="outside derivative"):
        content.slice(digest, 3, 10)


def test_slice_splitting_a_character_rejected(content: ContentStore) -> None:
    digest = content.put("é")
    with pytest.raises(ContentError, match="splits a UTF-8 character"):
        content.slice(digest, 0, 1)
