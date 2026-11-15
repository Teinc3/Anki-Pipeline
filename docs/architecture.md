# Architecture & API Reference

## Directory Layout

| Path | Responsibility |
| --- | --- |
| `prompts/*.md` | System instructions for OCR, root extraction, selection, and card generation |
| `src/schema.py` | Pydantic models for Gemini Structured Outputs and FastAPI payloads |
| `src/services/gemini.py` | Async `google-genai` Interactions API wrapper for `gemini-4-hydrogen` |
| `src/services/anki.py` | Async lock + `asyncio.to_thread` wrapper over `anki.collection.Collection` |
| `src/app.py` | FastAPI endpoints, HMAC session cookie auth, and static dashboard serving |
| `src/main.py` | Uvicorn application entrypoint |
| `src/static/*` | Vanilla HTML/CSS/JS dashboard with clipboard image paste and review table |
| `tests/test_pipeline.py` | Pytest suite with mocked Gemini calls and isolated temporary Anki SQLite DBs |

## Anki Sync Strategy

Direct SQLite mutation inside a remote or shared `anki-sync-server` container skips Anki's Update Sequence Number (`usn`) state and omits `cards` row creation from note type templates. Instead, `src/services/anki.py` maintains a local collection replica (`/data/collection.anki2` in Docker) via the official `anki` Python package:

1. `query_existing_and_cefr` pulls upstream changes from `ANKI_SERVER`, resolves exact field indices from `col.models.field_map(model)` for the `Rulang` note type, normalizes roots by stripping combining acute accents (`U+0301`) and mapping `ё` to `е`, and counts `CEFR_Level` values for notes whose creation timestamp (`notes.id` in epoch ms) falls within the last 60 days.
2. `add_cards_and_sync` pulls latest state, instantiates notes via `col.new_note(model)`, adds them to deck `Rulang` via `col.add_note(note, deck_id)`, and pushes deltas back to `ANKI_SERVER`.

## API Endpoints

### `POST /api/login`

Validates `password` against `ANKI_PASSWORD` with `hmac.compare_digest` and sets an `HttpOnly` signed cookie (`anki_session`).

### `GET /api/auth-status`

Returns `{ "authenticated": bool }` so the frontend chooses between the login screen and dashboard.

### `POST /api/logout`

Clears the `anki_session` cookie.

### `POST /api/extract`

Multipart form endpoint accepting optional `text` and `image`:

1. Runs `prompts/ocr.md` if an image is attached; returns HTTP 400 if combined Russian text is empty.
2. Runs `prompts/roots.md` (`thinking_level="high"`) returning indexed `sentences` and unique `words` to save output tokens.
3. Queries `query_existing_and_cefr` and filters out words already present in `Rulang`.
4. Runs `prompts/selection.md` (`thinking_level="medium"`) on missing candidates, forcing `selected=True` for user-bolded targets, and returns `selected_words`, `skipped_words`, `existing_words`, and `cefr_distribution`.

### `POST /api/generate`

Accepts the Stage 1 approved word list, chunks it into concurrent batches of 5 to avoid long-context drift, and calls `prompts/cards.md` (`thinking_level="high"`). Returns the 11-field `AnkiCard` array for Stage 2 table preview without writing to Anki.

### `POST /api/commit`

Accepts the final approved (and optionally inline-edited) `AnkiCard` rows from the Stage 2 table, writes and syncs them via `add_cards_and_sync`, and returns `{ "added_count", "tsv" }`.

## Prompt Customization

Edit the markdown files under `prompts/` directly without changing Python code:

| Prompt | Model & Reasoning | Output Mode |
| --- | --- | --- |
| `prompts/ocr.md` | `gemini-4-hydrogen` (`low`) | Plain Russian text |
| `prompts/roots.md` | `gemini-4-hydrogen` (`high`) | `ExtractionResult` JSON |
| `prompts/selection.md` | `gemini-4-hydrogen` (`medium`) | `SelectionResult` JSON |
| `prompts/cards.md` | `gemini-4-hydrogen` (`high`) | `CardBatchResult` JSON |
