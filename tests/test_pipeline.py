import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from anki.collection import Collection

from src.app import app
from src.schema import (
    AnkiCard,
    ApprovedWordInput,
    ExtractedWord,
    ExtractionResult,
    SelectionResult,
    WordDecision,
)
from src.services import anki, gemini


def _make_card(word: str = "подтверждать", cefr: str = "B2") -> AnkiCard:
    """Build a valid 11-field AnkiCard fixture for tests."""

    return AnkiCard(
        Target_Word=word, Declined_Form="подтверждаю",
        Word_Meaning="confirm, verify", Word_Meaning_ZH="確認",
        Word_Formation=f"{word} | подтвержда́ю -- Prefix: под- (under)",
        CEFR_Level=cefr, Grammar_Metadata="Verb, Impf, 1st, Sg, Pres",
        Sentence_RU="Я <b>подтверждаю</b> заявку.",
        Sentence_EN="I confirm the request.",
        Extra_Example_RU="Он <b>подтвердил</b> бан читера.",
        Extra_Example_EN="He confirmed the cheater ban."
    )


def _seed_rulang_collection(db_path: str, old_days: int = 90) -> None:
    """Create a temporary Rulang collection with recent and aged notes."""

    col = Collection(db_path)
    try:
        model = col.models.new(anki.NOTE_TYPE)
        for field in anki.CARD_FIELDS:
            col.models.add_field(model, col.models.new_field(field))
        tmpl = col.models.new_template("Card 1")
        tmpl["qfmt"] = "{{Target_Word}}"
        tmpl["afmt"] = "{{Word_Meaning}}"
        col.models.add_template(model, tmpl)
        col.models.add_dict(model)

        saved_model = col.models.by_name(anki.NOTE_TYPE)
        deck_id = col.decks.id(anki.DECK_NAME)
        assert saved_model is not None and deck_id is not None
        assert col.db is not None

        for word, cefr in (("решение", "B2"), ("читер", "B1")):
            note = col.new_note(saved_model)
            note["Target_Word"] = word
            note["CEFR_Level"] = cefr
            col.add_note(note, deck_id)

        old_note = col.new_note(saved_model)
        old_note["Target_Word"] = "старый"
        old_note["CEFR_Level"] = "C1"
        col.add_note(old_note, deck_id)
        old_ms = int((time.time() - old_days * 86400) * 1000)
        col.db.execute(
            "UPDATE notes SET id = ? WHERE id = ?", old_ms, old_note.id
        )
    finally:
        col.close()


def test_normalize_word() -> None:
    """Verify accent stripping, HTML cleanup, lowercasing, and yo->ye."""

    assert anki.normalize_word("<b>отка́зываться</b>") == "отказываться"
    assert anki.normalize_word(" Ещё ") == "еще"


async def test_anki_sqlite_query_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify root lookup, 60-day CEFR grouping, note commit, and TSV."""

    for var in ("ANKI_SERVER", "ANKI_USER", "ANKI_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    db_path = str(tmp_path / "collection.anki2")
    _seed_rulang_collection(db_path)

    existing, cefr = await anki.query_existing_and_cefr(
        ["реше́ние", "новое"], days=60, db_path=db_path
    )
    assert existing == { "реше́ние" }
    assert cefr == { "B1": 1, "B2": 1 }

    card = _make_card("подтверждать", "B2")
    added = await anki.add_cards_and_sync([card], db_path=db_path)
    assert added == 1

    existing_after, cefr_after = await anki.query_existing_and_cefr(
        ["подтверждать"], days=60, db_path=db_path
    )
    assert existing_after == { "подтверждать" }
    assert cefr_after == { "B1": 1, "B2": 2 }

    tsv = anki.format_cards_tsv([card])
    assert len(tsv.split("\t")) == 11


async def test_gemini_services_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Gemini OCR empty handling, root dedup, selection, and batching."""

    mock_create = AsyncMock(side_effect=[
        SimpleNamespace(output_text='""'),
        SimpleNamespace(output_text="Привет мир")
    ])
    client = SimpleNamespace(
        aio=SimpleNamespace(interactions=SimpleNamespace(create=mock_create))
    )
    monkeypatch.setattr(gemini, "_get_client", lambda: client)

    ocr_empty = await gemini.extract_russian_from_image(b"img", "image/png")
    assert ocr_empty == ""
    assert await gemini.extract_russian_from_image(
        b"img", "image/png"
    ) == "Привет мир"

    dup_payload = {
        "sentences": ["Первое решение важно."],
        "words": [
            {
                "target_word": "решение",
                "declined_form": "решение",
                "sentence_index": 0,
                "user_bolded": False
            },
            {
                "target_word": "реше́ние",
                "declined_form": "решении",
                "sentence_index": 0,
                "user_bolded": True
            }
        ]
    }
    mock_create.side_effect = [
        SimpleNamespace(output_text=json.dumps(dup_payload))
    ]
    extracted = await gemini.extract_unique_roots("text")
    assert len(extracted.words) == 1
    assert extracted.words[0].user_bolded is True

    empty_sel = await gemini.select_words_to_add([], dict())
    assert empty_sel.decisions == []

    words = [
        ApprovedWordInput(
            target_word=f"слово{i}", declined_form=f"слово{i}",
            sentence_ru=f"Это слово{i}."
        )
        for i in range(7)
    ]
    batch_responses = [
        SimpleNamespace(output_text=json.dumps({
            "cards": [
                _make_card(w.target_word).model_dump() for w in words[i:i + 3]
            ]
        }))
        for i in range(0, 7, 3)
    ]
    mock_create.side_effect = batch_responses
    cards = await gemini.generate_flashcards(words, batch_size=3)
    assert len(cards) == 7
    assert [c.Target_Word for c in cards] == [f"слово{i}" for i in range(7)]


async def test_fastapi_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify auth, empty-OCR 400 bounce, extract, preview, and commit."""

    db_path = str(tmp_path / "app.anki2")
    _seed_rulang_collection(db_path)
    monkeypatch.setenv("ANKI_PASSWORD", "test-pass")
    monkeypatch.setenv("ANKI_COLLECTION_PATH", db_path)
    for var in ("ANKI_SERVER", "ANKI_USER"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setattr(
        gemini, "extract_russian_from_image", AsyncMock(return_value="")
    )
    ext_mock = AsyncMock(return_value=ExtractionResult(
        sentences=["Я подтверждаю это решение."],
        words=[
            ExtractedWord(
                target_word="решение", declined_form="решение",
                sentence_index=0, user_bolded=False
            ),
            ExtractedWord(
                target_word="подтверждать", declined_form="подтверждаю",
                sentence_index=0, user_bolded=True
            ),
            ExtractedWord(
                target_word="я", declined_form="Я",
                sentence_index=0, user_bolded=False
            )
        ]
    ))
    monkeypatch.setattr(gemini, "extract_unique_roots", ext_mock)
    sel_mock = AsyncMock(return_value=SelectionResult(decisions=[
        WordDecision(
            target_word="подтверждать", cefr_level="B2",
            brief_meaning="confirm", selected=False
        ),
        WordDecision(
            target_word="я", cefr_level="A1", brief_meaning="I", selected=False
        )
    ]))
    monkeypatch.setattr(gemini, "select_words_to_add", sel_mock)
    monkeypatch.setattr(
        gemini, "generate_flashcards", AsyncMock(return_value=[_make_card()])
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        unauth = await client.post("/api/extract", data={ "text": "тест" })
        assert unauth.status_code == 401

        bad_login = await client.post("/api/login", json={ "password": "nope" })
        assert bad_login.status_code == 401

        ok_login = await client.post(
            "/api/login", json={ "password": "test-pass" }
        )
        assert ok_login.status_code == 200

        empty_img = await client.post(
            "/api/extract",
            files={ "image": ("empty.png", b"pngbytes", "image/png") }
        )
        assert empty_img.status_code == 400

        ext_res = await client.post(
            "/api/extract", data={ "text": "Я **подтверждаю** это решение." }
        )
        assert ext_res.status_code == 200
        body = ext_res.json()
        assert body["existing_words"] == ["решение"]
        assert body["cefr_distribution"] == { "B1": 1, "B2": 1 }
        selected_targets = [w["target_word"] for w in body["selected_words"]]
        assert selected_targets == ["подтверждать"]
        assert [w["target_word"] for w in body["skipped_words"]] == ["я"]

        gen_res = await client.post("/api/generate", json={
            "words": [
                {
                    "target_word": "подтверждать",
                    "declined_form": "подтверждаю",
                    "sentence_ru": "Я подтверждаю это решение."
                }
            ],
            "skipped_words": ["я"]
        })
        assert gen_res.status_code == 200
        preview_cards = gen_res.json()["cards"]
        assert len(preview_cards) == 1

        ignored_check, _ = await anki.query_existing_and_cefr(
            ["Я"], days=60, db_path=db_path
        )
        assert ignored_check == { "Я" }

        before_commit, _ = await anki.query_existing_and_cefr(
            ["подтверждать"], days=60, db_path=db_path
        )
        assert before_commit == set()

        com_res = await client.post(
            "/api/commit", json={ "cards": preview_cards }
        )
        assert com_res.status_code == 200
        assert com_res.json()["added_count"] == 1
        assert "подтверждать\t" in com_res.json()["tsv"]

        after_commit, _ = await anki.query_existing_and_cefr(
            ["подтверждать"], days=60, db_path=db_path
        )
        assert after_commit == { "подтверждать" }


__all__ = [
    "test_anki_sqlite_query_and_commit", "test_fastapi_endpoints",
    "test_gemini_services_mocked", "test_normalize_word"
]
