import asyncio
import base64
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal
from google import genai
from google.genai._gaos.types import UnrecognizedStr

from src.schema import (
    AnkiCard,
    ApprovedWordInput,
    CardBatchResult,
    ExtractionResult,
    SelectionResult,
)
from src.services.anki import normalize_word


MODEL_ID: Literal["gemini-4-hydrogen"] = "gemini-4-hydrogen"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@lru_cache(maxsize=8)
def _load_prompt(filename: str) -> str:
    """Read and cache a system prompt markdown file from prompts/."""

    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    """Initialize and cache the default Gemini API client."""

    return genai.Client()


async def _generate_json(
    prompt_file: str,
    user_input: str,
    thinking_level: Literal["low", "medium", "high"],
    schema: dict[str, object]
) -> str:
    """Run one structured JSON Gemini interaction and return output text."""

    interaction = await _get_client().aio.interactions.create(
        model=MODEL_ID,
        system_instruction=_load_prompt(prompt_file),
        input=user_input,
        generation_config={ "thinking_level": thinking_level },
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": schema
        }
    )
    return (interaction.output_text or "").strip()


async def extract_russian_from_image(image_bytes: bytes, mime_type: str) -> str:
    """Step 1: OCR an image into Russian-only text using low reasoning."""

    b64_data = base64.b64encode(image_bytes).decode("ascii")
    interaction = await _get_client().aio.interactions.create(
        model=MODEL_ID,
        system_instruction=_load_prompt("ocr.md"),
        input=[
            { "type": "text", "text": "Extract Russian text only." },
            {
                "type": "image",
                "data": b64_data,
                "mime_type": UnrecognizedStr(mime_type)
            }
        ],
        generation_config={ "thinking_level": "low" }
    )
    cleaned = (interaction.output_text or "").strip()
    if cleaned in { '""', "''" }:
        return ""
    return cleaned


async def extract_unique_roots(russian_text: str) -> ExtractionResult:
    """Step 2: Segment sentences and deduplicate base Russian roots."""

    raw = await _generate_json(
        "roots.md", russian_text, "high", ExtractionResult.model_json_schema()
    )
    parsed = ExtractionResult.model_validate_json(
        raw or '{"sentences": [], "words": []}'
    )
    deduped = dict()
    for word in parsed.words:
        key = normalize_word(word.target_word)
        if not key:
            continue
        if key not in deduped:
            deduped[key] = word
        elif word.user_bolded:
            deduped[key].user_bolded = True
    return ExtractionResult(
        sentences=parsed.sentences, words=list(deduped.values())
    )


async def select_words_to_add(
    missing_words: list[dict[str, object]], cefr_dist: dict[str, int]
) -> SelectionResult:
    """Step 5: Curate unknown roots using 60-day CEFR stats (medium)."""

    if not missing_words:
        return SelectionResult(decisions=[])
    payload = json.dumps(
        { "cefr_distribution_60d": cefr_dist, "candidates": missing_words },
        ensure_ascii=False
    )
    raw = await _generate_json(
        "selection.md", payload, "medium", SelectionResult.model_json_schema()
    )
    return SelectionResult.model_validate_json(raw or '{"decisions": []}')


async def _generate_batch(batch: list[ApprovedWordInput]) -> list[AnkiCard]:
    """Generate one small batch of 11-field cards with high reasoning."""

    items = [w.model_dump() for w in batch]
    payload = json.dumps(items, ensure_ascii=False)
    raw = await _generate_json(
        "cards.md", payload, "high", CardBatchResult.model_json_schema()
    )
    parsed = CardBatchResult.model_validate_json(raw or '{"cards": []}')
    return parsed.cards


async def generate_flashcards(
    approved_words: list[ApprovedWordInput], batch_size: int = 5
) -> list[AnkiCard]:
    """Step 6: Generate flashcards in concurrent batches to curb drift."""

    if not approved_words:
        return []
    batches = [
        approved_words[i:i + batch_size]
        for i in range(0, len(approved_words), batch_size)
    ]
    results = await asyncio.gather(
        *[_generate_batch(chunk) for chunk in batches]
    )
    return [card for group in results for card in group]


__all__ = [
    "MODEL_ID", "PROMPTS_DIR", "extract_russian_from_image",
    "extract_unique_roots", "generate_flashcards", "select_words_to_add"
]
