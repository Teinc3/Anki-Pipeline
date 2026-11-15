import asyncio
import os
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path
from anki.collection import Collection
from anki.sync_pb2 import SyncAuth

from src.schema import AnkiCard


DECK_NAME = "Rulang"
NOTE_TYPE = "Rulang"
FIELD_SEP = "\x1f"
TAG_RE = re.compile(r"<[^>]+>")
CARD_FIELDS = (
    "Target_Word", "Declined_Form", "Word_Meaning", "Word_Meaning_ZH",
    "Word_Formation", "CEFR_Level", "Grammar_Metadata", "Sentence_RU",
    "Sentence_EN", "Extra_Example_RU", "Extra_Example_EN"
)
# ponytail: global async lock serializes single-user collection access
_COL_LOCK = asyncio.Lock()


def normalize_word(word: str) -> str:
    """Strip HTML tags, combining accents (U+0301), and normalize yo/case."""

    plain = TAG_RE.sub("", word).strip().lower()
    decomposed = unicodedata.normalize("NFD", plain)
    no_stress = "".join(ch for ch in decomposed if ch != "\u0301")
    return unicodedata.normalize("NFC", no_stress).replace("ё", "е")


def format_cards_tsv(cards: list[AnkiCard]) -> str:
    """Format flashcards as a clean 11-column TSV string for Anki backup."""

    lines = []
    for card in cards:
        data = card.model_dump()
        row = [
            re.sub(r"[\t\r\n]+", " ", str(data[col])).strip()
            for col in CARD_FIELDS
        ]
        lines.append("\t".join(row))
    return "\n".join(lines)


def _collection_path(db_path: str | None = None) -> str:
    """Resolve active SQLite collection path from arg or environment."""

    return db_path or os.environ.get(
        "ANKI_COLLECTION_PATH", "data/collection.anki2"
    )


def _ignored_path(db_path: str | None = None) -> Path:
    """Return path to ignored_words.txt alongside collection.anki2."""

    return Path(_collection_path(db_path)).resolve().with_name(
        "ignored_words.txt"
    )


def _load_ignored(db_path: str | None = None) -> set[str]:
    """Load normalized ignored roots from ignored_words.txt."""

    path = _ignored_path(db_path)
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return { normalize_word(line) for line in lines if normalize_word(line) }


def _save_ignored_sync(
    words: list[str], db_path: str | None = None
) -> None:
    """Merge and persist normalized ignored roots to ignored_words.txt."""

    added = { normalize_word(w) for w in words if normalize_word(w) }
    if not added:
        return
    path = _ignored_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = sorted(_load_ignored(db_path) | added)
    path.write_text("\n".join(merged) + "\n", encoding="utf-8")


def _sync_credentials() -> tuple[str, str, str] | None:
    """Return configured (ANKI_SERVER, ANKI_USER, ANKI_PASSWORD) or None."""

    server = os.environ.get("ANKI_SERVER", "").strip()
    user = os.environ.get("ANKI_USER", "").strip()
    password = os.environ.get("ANKI_PASSWORD", "").strip()
    if not (server and user and password):
        return None
    return server, user, password


def _sync_collection(
    col: Collection, auth: SyncAuth | None = None
) -> SyncAuth | None:
    """Sync local collection replica with the configured Anki Sync Server."""

    if auth is None:
        creds = _sync_credentials()
        if creds is None:
            return None
        server, user, password = creds
        auth = col.sync_login(user, password, server)
    res = col.sync_collection(auth, sync_media=False)
    if res.new_endpoint:
        auth.endpoint = res.new_endpoint
    if res.required in (res.FULL_SYNC, res.FULL_DOWNLOAD):
        col.close_for_full_sync()
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        col.reopen(after_full_sync=True)
    elif res.required == res.FULL_UPLOAD:
        col.close_for_full_sync()
        col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
        col.reopen(after_full_sync=True)
    return auth


def _open_collection(db_path: str | None = None) -> Collection:
    """Ensure parent directory exists and open the local Anki collection."""

    col_path = _collection_path(db_path)
    os.makedirs(os.path.dirname(col_path) or ".", exist_ok=True)
    return Collection(col_path)


def _query_sync(
    candidate_roots: list[str], days: int, db_path: str | None
) -> tuple[set[str], dict[str, int]]:
    """Sync collection and query existing/ignored roots + 60-day CEFR stats."""

    existing_norm = _load_ignored(db_path)
    col = _open_collection(db_path)
    try:
        _sync_collection(col)
        model = col.models.by_name(NOTE_TYPE)
        if not model or not col.db:
            matched = {
                w for w in candidate_roots
                if normalize_word(w) in existing_norm
            }
            return matched, dict()
        fmap = col.models.field_map(model)
        word_idx = fmap["Target_Word"][0] if "Target_Word" in fmap else None
        cefr_idx = fmap["CEFR_Level"][0] if "CEFR_Level" in fmap else None
        if word_idx is None:
            matched = {
                w for w in candidate_roots
                if normalize_word(w) in existing_norm
            }
            return matched, dict()

        rows = col.db.all(
            "SELECT id, flds FROM notes WHERE mid = ?", model["id"]
        )
        cutoff_ms = int((time.time() - days * 86400) * 1000)
        cefr_counts: Counter[str] = Counter()

        for note_id, flds in rows:
            parts = flds.split(FIELD_SEP)
            if len(parts) > word_idx and parts[word_idx].strip():
                existing_norm.add(normalize_word(parts[word_idx]))
            if note_id < cutoff_ms or cefr_idx is None:
                continue
            lvl = parts[cefr_idx].strip() if len(parts) > cefr_idx else ""
            cefr_counts[lvl or "UNSET"] += 1

        matched = {
            w for w in candidate_roots if normalize_word(w) in existing_norm
        }
        return matched, dict(sorted(cefr_counts.items()))
    finally:
        col.close()


def _commit_sync(cards: list[AnkiCard], db_path: str | None) -> int:
    """Add approved cards to Rulang deck in local collection and sync."""

    if not cards:
        return 0
    col = _open_collection(db_path)
    try:
        auth = _sync_collection(col)
        model = col.models.by_name(NOTE_TYPE)
        if not model:
            raise RuntimeError(
                f"Note type '{NOTE_TYPE}' not found in Anki collection."
            )
        deck_id = col.decks.id(DECK_NAME)
        if deck_id is None:
            raise RuntimeError(f"Deck '{DECK_NAME}' not found.")
        added = 0
        for card in cards:
            note = col.new_note(model)
            for field_name, val in card.model_dump().items():
                if field_name in note:
                    note[field_name] = val
            col.add_note(note, deck_id)
            added += 1
        if auth is not None:
            _sync_collection(col, auth)
        return added
    finally:
        col.close()


async def query_existing_and_cefr(
    candidate_roots: list[str], days: int = 60, db_path: str | None = None
) -> tuple[set[str], dict[str, int]]:
    """Return subset of candidate_roots in Anki/ignored and N-day CEFR stats."""

    async with _COL_LOCK:
        return await asyncio.to_thread(
            _query_sync, candidate_roots, days, db_path
        )


async def add_ignored_words(
    words: list[str], db_path: str | None = None
) -> None:
    """Persist skipped or declined words into ignored_words.txt."""

    async with _COL_LOCK:
        await asyncio.to_thread(_save_ignored_sync, words, db_path)


async def add_cards_and_sync(
    cards: list[AnkiCard], db_path: str | None = None
) -> int:
    """Insert generated 11-field cards into Rulang and sync to ANKI_SERVER."""

    async with _COL_LOCK:
        return await asyncio.to_thread(_commit_sync, cards, db_path)


__all__ = [
    "CARD_FIELDS", "DECK_NAME", "NOTE_TYPE", "add_cards_and_sync",
    "add_ignored_words", "format_cards_tsv", "normalize_word",
    "query_existing_and_cefr"
]
