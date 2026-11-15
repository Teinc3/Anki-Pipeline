import hashlib
import hmac
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.schema import (
    CandidateWordView,
    CommitRequest,
    CommitResponse,
    ExtractionResult,
    ExtractResponse,
    GenerateRequest,
    GenerateResponse,
    LoginRequest,
    WordDecision,
)
from src.services import anki, gemini


load_dotenv()

SESSION_COOKIE = "anki_session"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Anki Russian Pipeline")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _expected_password() -> str:
    """Return configured dashboard/sync password from environment."""

    return os.environ.get("ANKI_PASSWORD", "").strip()


def _session_token(secret: str) -> str:
    """Derive deterministic HMAC token from configured password."""

    return hmac.new(
        secret.encode("utf-8"), b"anki-pipeline-session", hashlib.sha256
    ).hexdigest()


def _is_authenticated(request: Request) -> bool:
    """Check whether request carries a valid session cookie."""

    expected = _expected_password()
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if not expected or not cookie:
        return False
    return hmac.compare_digest(cookie, _session_token(expected))


def require_auth(request: Request) -> None:
    """FastAPI dependency enforcing authenticated dashboard access."""

    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _safe_sentence(sentences: list[str], index: int) -> str:
    """Resolve sentence by index with bounds protection."""

    if 0 <= index < len(sentences):
        return sentences[index]
    return sentences[0] if sentences else ""


async def _resolve_input_text(text: str, image: UploadFile | None) -> str:
    """Run Step 1 OCR when an image is provided and combine with pasted text."""

    parts = [text.strip()] if text.strip() else []
    if image is not None and image.filename:
        raw = await image.read()
        if raw:
            mime = image.content_type or "image/png"
            ocr_text = await gemini.extract_russian_from_image(raw, mime)
            if not ocr_text and not parts:
                raise HTTPException(
                    status_code=400,
                    detail="No Russian text detected in uploaded image."
                )
            if ocr_text:
                parts.append(ocr_text)
    combined = "\n".join(parts).strip()
    if not combined:
        raise HTTPException(
            status_code=400,
            detail="Provide Russian text or an image containing Russian text."
        )
    return combined


def _partition_words(
    extracted: ExtractionResult, existing_roots: set[str]
) -> tuple[list[str], list[dict[str, object]]]:
    """Split extracted roots into existing Anki words and missing candidates."""

    existing_norm = { anki.normalize_word(w) for w in existing_roots }
    existing_words: list[str] = []
    missing: list[dict[str, object]] = []
    for word in extracted.words:
        if anki.normalize_word(word.target_word) in existing_norm:
            existing_words.append(word.target_word)
            continue
        sentence_ru = _safe_sentence(extracted.sentences, word.sentence_index)
        missing.append({
            "target_word": word.target_word,
            "declined_form": word.declined_form,
            "sentence_ru": sentence_ru,
            "user_bolded": word.user_bolded
        })
    return existing_words, missing


def _merge_selection(
    missing: list[dict[str, object]], decisions: list[WordDecision]
) -> tuple[list[CandidateWordView], list[CandidateWordView]]:
    """Combine missing candidate info with Step 5 LLM curation decisions."""

    decision_map = {
        anki.normalize_word(d.target_word): d for d in decisions
    }
    selected_words: list[CandidateWordView] = []
    skipped_words: list[CandidateWordView] = []
    for item in missing:
        target = str(item["target_word"])
        dec = decision_map.get(anki.normalize_word(target))
        bolded = bool(item["user_bolded"])
        is_selected = True if bolded else (dec.selected if dec else True)
        view = CandidateWordView(
            target_word=target,
            declined_form=str(item["declined_form"]),
            sentence_ru=str(item["sentence_ru"]),
            cefr_level=dec.cefr_level if dec else "B1",
            brief_meaning=dec.brief_meaning if dec else "",
            selected=is_selected,
            user_bolded=bolded
        )
        if is_selected:
            selected_words.append(view)
        else:
            skipped_words.append(view)
    return selected_words, skipped_words


@app.get("/")
async def index() -> FileResponse:
    """Serve the single-page dashboard frontend."""

    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/auth-status")
async def auth_status(request: Request) -> dict[str, bool]:
    """Return current session authentication status."""

    return { "authenticated": _is_authenticated(request) }


@app.post("/api/login")
async def login(req: LoginRequest, response: Response) -> dict[str, bool]:
    """Authenticate with ANKI_PASSWORD and issue HttpOnly session cookie."""

    expected = _expected_password()
    if not expected or not hmac.compare_digest(req.password.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid password.")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=_session_token(expected),
        httponly=True,
        samesite="lax"
    )
    return { "ok": True }


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, bool]:
    """Clear active session cookie."""

    response.delete_cookie(SESSION_COOKIE)
    return { "ok": True }


@app.post("/api/extract", response_model=ExtractResponse)
async def extract_pipeline(
    text: str = Form(""), image: UploadFile | None = File(None),
    _: None = Depends(require_auth)
) -> ExtractResponse:
    """Run Steps 1-5: OCR, root segmentation, Anki lookup, and selection."""

    combined = await _resolve_input_text(text, image)
    extracted = await gemini.extract_unique_roots(combined)
    if not extracted.words:
        return ExtractResponse(
            extracted_text=combined, sentences=extracted.sentences,
            selected_words=[], skipped_words=[], existing_words=[],
            cefr_distribution=dict()
        )
    roots = [w.target_word for w in extracted.words]
    existing_roots, cefr_dist = await anki.query_existing_and_cefr(
        roots, days=60
    )
    existing_words, missing = _partition_words(extracted, existing_roots)
    sel_result = await gemini.select_words_to_add(missing, cefr_dist)
    selected_words, skipped_words = _merge_selection(
        missing, sel_result.decisions
    )
    return ExtractResponse(
        extracted_text=combined, sentences=extracted.sentences,
        selected_words=selected_words, skipped_words=skipped_words,
        existing_words=existing_words, cefr_distribution=cefr_dist
    )


@app.post("/api/generate", response_model=GenerateResponse)
async def generate_preview(
    req: GenerateRequest, _: None = Depends(require_auth)
) -> GenerateResponse:
    """Persist skipped words and run Step 6 in batches for Stage 2 review."""

    if req.skipped_words:
        await anki.add_ignored_words(req.skipped_words)
    if not req.words:
        raise HTTPException(
            status_code=400, detail="Select at least one word to generate."
        )
    cards = await gemini.generate_flashcards(req.words)
    return GenerateResponse(cards=cards)


@app.post("/api/commit", response_model=CommitResponse)
async def commit_cards(
    req: CommitRequest, _: None = Depends(require_auth)
) -> CommitResponse:
    """Write user-approved cards to Anki, sync, and return TSV backup."""

    if not req.cards:
        raise HTTPException(
            status_code=400, detail="Approve at least one card first."
        )
    if req.declined_words:
        await anki.add_ignored_words(req.declined_words)
    added = await anki.add_cards_and_sync(req.cards)
    return CommitResponse(
        added_count=added, tsv=anki.format_cards_tsv(req.cards)
    )


__all__ = ["app"]
