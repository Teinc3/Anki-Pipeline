from typing import Any
import requests


ANKI_CONNECT_URL = "http://localhost:8765"
DECK_NAME = "Rulang"
NOTE_TYPE = "Rulang"
FIELD_NAME = "Grammar_Metadata"


def invoke(action: str, **params: Any) -> Any:
    """Send a single action request to AnkiConnect and return its result."""

    resp = requests.post(
        ANKI_CONNECT_URL,
        json={
            "action": action,
            "version": 6,
            "params": params
        },
        timeout=10
    ).json()
    if resp.get("error"):
        raise RuntimeError(f"AnkiConnect error on {action}: {resp['error']}")
    return resp["result"]


def clean_grammar_metadata() -> int:
    """Strip leading commas and spaces from Rulang Grammar_Metadata fields."""

    query = f'"deck:{DECK_NAME}" "note:{NOTE_TYPE}"'
    note_ids = invoke("findNotes", query=query)
    if not note_ids:
        return 0

    notes = invoke("notesInfo", notes=note_ids)
    updated = 0
    for note in notes:
        raw = note["fields"].get(FIELD_NAME, dict()).get("value", "")
        cleaned = raw.lstrip(", ")
        if cleaned == raw:
            continue
        invoke(
            "updateNoteFields",
            note={
                "id": note["noteId"],
                "fields": { FIELD_NAME: cleaned }
            }
        )
        updated += 1
    return updated


if __name__ == "__main__":
    count = clean_grammar_metadata()
    print(f"Updated {count} notes.")


__all__ = ["clean_grammar_metadata", "invoke"]
