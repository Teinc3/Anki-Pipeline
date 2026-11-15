# Anki Pipeline

Multimodal FastAPI dashboard that converts text and screenshots into 11-field Anki flashcards, syncs against an Anki Sync Server, and provides a two-stage HITL review workflow before committing.

## Pipeline Overview

### Stage 1: Extraction & Word Curation

1. If an image is uploaded, `gemini-4-hydrogen` (`thinking_level="low"`) extracts Russian-only text segments and bounces an HTTP 400 error if no Russian text is found.
2. `gemini-4-hydrogen` (`thinking_level="high"`) splits the Russian text into indexed sentences and deduplicates words into canonical dictionary roots (`Target_Word`), recording `Declined_Form`, `sentence_index`, and `user_bolded`.
3. The local Anki replica syncs with `ANKI_SERVER` and queries `Target_Word` membership plus a 60-day `CEFR_Level` note distribution in SQLite.
4. `gemini-4-hydrogen` (`thinking_level="medium"`) uses the 60-day CEFR distribution to propose which missing words to add while respecting `user_bolded` overrides.

### Stage 2: Batched Card Generation & Table Review

1. Approved words are split into concurrent batches of 5 and sent to `gemini-4-hydrogen` (`thinking_level="high"`) with strict JSON schema output.
2. All 11 fields (`Target_Word`, `Declined_Form`, `Word_Meaning`, `Word_Meaning_ZH`, `Word_Formation`, `CEFR_Level`, `Grammar_Metadata`, `Sentence_RU`, `Sentence_EN`, `Extra_Example_RU`, `Extra_Example_EN`) render in an editable table with per-row Approve/Decline toggles.
3. Only explicitly approved rows are inserted into `Rulang`, synced back to `ANKI_SERVER`, and exported as a downloadable TSV backup.

## Configuration

Add the following variables to `.env`:

| Variable | Purpose | Example |
| --- | --- | --- |
| `GEMINI_API_KEY` | Google GenAI API key | `AIza...` |
| `ANKI_SERVER` | Self-hosted Anki Sync Server URL | `https://teinc3.net/anki/` |
| `ANKI_USER` | Anki sync username | `teinc3` |
| `ANKI_PASSWORD` | Anki sync password and dashboard login password | `secret` |
| `ANKI_COLLECTION_PATH` | Optional path to local `.anki2` replica | `data/collection.anki2` |

## Running Locally

```sh
uv sync
uv run python -m src.main
```

Open `http://localhost:8000` and unlock the dashboard using `ANKI_PASSWORD`.

## Running with Docker

```sh
docker compose up --build -d
```

The container persists `/data/collection.anki2` in the `anki_data` Docker volume so incremental sync with `ANKI_SERVER` stays fast across restarts.

## Testing & Linting

```sh
.venv/bin/ruff check --fix
.venv/bin/pytest
```

See [docs/architecture.md](docs/architecture.md) for module responsibilities, API contracts, and prompt design details.
