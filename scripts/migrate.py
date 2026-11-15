import re
from collections import Counter
import requests


ANKI_CONNECT_URL = "http://localhost:8765"
DECK_NAME = "Rulang"               # Target deck name
NOTE_TYPE = "Rulang"               # Exact Note Type name
WORD_FIELD = "Target_Word"         # Field shown for context in manual loop
SOURCE_FIELD = "Grammar_Metadata"  # Field currently containing [A1], [C2], etc.
TARGET_FIELD = "CEFR_Level"        # New field to populate

# Matches "[A1]".."[C2]" including brackets; group(1) captures only "A1".."C2"
LEVEL_RE = re.compile(r"\[(A[12]|B[12]|C[12])\]", re.IGNORECASE)
VALID_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}


def invoke(action: str, **params):
    resp = requests.post(
        ANKI_CONNECT_URL,
        json={"action": action, "version": 6, "params": params},
        timeout=10,
    ).json()
    if resp.get("error"):
        raise RuntimeError(f"AnkiConnect error on {action}: {resp['error']}")
    return resp["result"]


def ensure_level_field() -> None:
    fields = invoke("modelFieldNames", modelName=NOTE_TYPE)
    if TARGET_FIELD not in fields:
        invoke("modelFieldAdd", modelName=NOTE_TYPE, fieldName=TARGET_FIELD)
        print(f"Added field '{TARGET_FIELD}' to note type '{NOTE_TYPE}'.")


def backfill_and_resolve() -> tuple[int, int]:
    note_ids = invoke("findNotes", query=f'"deck:{DECK_NAME}" "note:{NOTE_TYPE}"')
    if not note_ids:
        return 0, 0

    notes = invoke("notesInfo", notes=note_ids)
    auto_updated = 0
    unresolved = []

    # Pass 1: Automatic regex extraction (with brackets) and pruning
    for note in notes:
        fields = note["fields"]
        raw_meta = fields.get(SOURCE_FIELD, {}).get("value", "")
        existing_level = fields.get(TARGET_FIELD, {}).get("value", "").strip()

        match = LEVEL_RE.search(raw_meta)
        if match:
            level = match.group(1).upper()                      # e.g. "A1" (no brackets)
            cleaned_meta = LEVEL_RE.sub("", raw_meta, count=1)  # removes "[A1]" completely

            invoke(
                "updateNoteFields",
                note={
                    "id": note["noteId"],
                    "fields": {
                        TARGET_FIELD: level,
                        SOURCE_FIELD: cleaned_meta,
                    },
                },
            )
            auto_updated += 1
        elif not existing_level:
            unresolved.append(note)

    # Pass 2: Interactive terminal loop for undetected notes
    manually_updated = 0
    total_unresolved = len(unresolved)

    if total_unresolved > 0:
        print(f"\n--- {total_unresolved} notes require manual resolution ---")
        print("(Press Ctrl+C at any time to stop; completed entries are saved immediately.)\n")

    for idx, note in enumerate(unresolved, start=1):
        fields = note["fields"]
        target_word = fields.get(WORD_FIELD, {}).get("value", "<missing>")
        current_meta = fields.get(SOURCE_FIELD, {}).get("value", "")

        print(f"[{idx}/{total_unresolved}] Note ID: {note['noteId']}")
        print(f"  {WORD_FIELD}:      {target_word}")
        print(f"  {SOURCE_FIELD}: {current_meta!r}")

        while True:
            new_level = input(f"  New {TARGET_FIELD} (A1-C2): ").strip().strip("[]").upper()
            if new_level in VALID_LEVELS:
                break
            print(f"  Invalid level '{new_level}'. Expected one of: {', '.join(sorted(VALID_LEVELS))}")

        meta_input = input(
            f"  New {SOURCE_FIELD} [Enter to keep current, 'CLEAR' to empty]: "
        )
        if meta_input == "":
            new_meta = current_meta
        elif meta_input == "CLEAR":
            new_meta = ""
        else:
            new_meta = meta_input

        invoke(
            "updateNoteFields",
            note={
                "id": note["noteId"],
                "fields": {
                    TARGET_FIELD: new_level,
                    SOURCE_FIELD: new_meta,
                },
            },
        )
        manually_updated += 1
        print("  -> Saved.\n")

    return auto_updated, manually_updated


def get_difficulty_distribution(days: int = 30) -> dict[str, int]:
    """Returns CEFR level counts for notes added in the last `days` days."""
    query = f'"deck:{DECK_NAME}" added:{days}'
    note_ids = invoke("findNotes", query=query)
    if not note_ids:
        return {}

    notes = invoke("notesInfo", notes=note_ids)
    counts = Counter()
    for note in notes:
        lvl = note["fields"].get(TARGET_FIELD, {}).get("value", "").strip()
        counts[lvl or "UNSET"] += 1

    return dict(sorted(counts.items()))


if __name__ == "__main__":
    ensure_level_field()
    try:
        auto_cnt, manual_cnt = backfill_and_resolve()
        print(f"Auto: {auto_cnt} notes | Manual: {manual_cnt} notes.")
    except KeyboardInterrupt:
        print("\nInterrupted by user. Progress up to this card has been saved.")
    print("Last 30 days distribution:", get_difficulty_distribution(30))
