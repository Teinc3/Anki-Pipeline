from pydantic import BaseModel


class ExtractedWord(BaseModel):
    """Single deduplicated root extracted from segmented Russian text."""

    target_word: str
    declined_form: str
    sentence_index: int
    user_bolded: bool = False


class ExtractionResult(BaseModel):
    """Step 2 output: indexed Russian sentences and unique roots."""

    sentences: list[str]
    words: list[ExtractedWord]


class WordDecision(BaseModel):
    """Step 5 curation decision for one candidate root."""

    target_word: str
    cefr_level: str
    brief_meaning: str
    selected: bool


class SelectionResult(BaseModel):
    """Step 5 model output wrapping all candidate decisions."""

    decisions: list[WordDecision]


class CandidateWordView(BaseModel):
    """Frontend payload item for Stage 1 selection toggle."""

    target_word: str
    declined_form: str
    sentence_ru: str
    cefr_level: str
    brief_meaning: str
    selected: bool
    user_bolded: bool = False


class ExtractResponse(BaseModel):
    """Full Steps 1-5 response sent to the Stage 1 selection UI."""

    extracted_text: str
    sentences: list[str]
    selected_words: list[CandidateWordView]
    skipped_words: list[CandidateWordView]
    existing_words: list[str]
    cefr_distribution: dict[str, int]


class ApprovedWordInput(BaseModel):
    """User-approved word item sent to Step 6 card generation."""

    target_word: str
    declined_form: str
    sentence_ru: str
    cefr_level: str = ""


class GenerateRequest(BaseModel):
    """Stage 1 approval payload requesting Step 6 card previews."""

    words: list[ApprovedWordInput]
    skipped_words: list[str] = []


class AnkiCard(BaseModel):
    """11-field flashcard matching the Anki Rulang note type."""

    Target_Word: str
    Declined_Form: str
    Word_Meaning: str
    Word_Meaning_ZH: str
    Word_Formation: str
    CEFR_Level: str
    Grammar_Metadata: str
    Sentence_RU: str
    Sentence_EN: str
    Extra_Example_RU: str
    Extra_Example_EN: str


class CardBatchResult(BaseModel):
    """Step 6 structured output wrapper for a batch of flashcards."""

    cards: list[AnkiCard]


class GenerateResponse(BaseModel):
    """Stage 2 preview response holding generated cards before commit."""

    cards: list[AnkiCard]


class CommitRequest(BaseModel):
    """Final user-approved cards to insert into Anki and sync."""

    cards: list[AnkiCard]
    declined_words: list[str] = []


class CommitResponse(BaseModel):
    """Result of committing approved cards to Anki."""

    added_count: int
    tsv: str


class LoginRequest(BaseModel):
    """Password authentication payload."""

    password: str


__all__ = [
    "AnkiCard", "ApprovedWordInput", "CandidateWordView", "CardBatchResult",
    "CommitRequest", "CommitResponse", "ExtractedWord", "ExtractResponse",
    "ExtractionResult", "GenerateRequest", "GenerateResponse", "LoginRequest",
    "SelectionResult", "WordDecision"
]
