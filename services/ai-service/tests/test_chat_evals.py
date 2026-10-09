"""The chat-ordering eval set and its grader (S17). The real-model run needs a key and a live
hub; here every phrasing goes through the whole draft pipeline with the extraction a correct
model would return, so 20/20 shows that dates, product rules and checks are right and that
the real run measures the model alone."""

from datetime import datetime
from typing import Any

import pytest
from app import chat, chat_evals
from app.config import Settings

from tests.fakes import (
    CANNULA_ID,
    KIT_A_ID,
    RDK_ID,
    USER_TOKEN,
    FakeHub,
    FakeProvider,
    extraction,
    q,
    text,
    when,
)

pytestmark = pytest.mark.anyio

CATALOG = {
    "SURG-KIT-A": {"id": KIT_A_ID, "default_min_shelf_life_days": 30},
    "DIAG-RDK": {"id": RDK_ID, "default_min_shelf_life_days": 60},
    "IV-CAN-20G": {"id": CANNULA_ID, "default_min_shelf_life_days": 30},
}
CRITICAL = "CRITICAL"


def prio(word: str) -> dict[str, str]:
    return {"value": CRITICAL, "quote": word}


IDEAL: dict[str, dict[str, Any]] = {
    "o01": extraction(
        products=["SK-A"],
        qty_required=q(850, "850"),
        required_by=when("weekday", "by fri", weekday="friday"),
        notes="for ICU",
    ),
    "o02": extraction(
        products=["Surgical Kit A"],
        qty_required=q(1000, "1000 nos"),
        qty_local_usable=q(150, "150 available with us"),
        priority=prio("critical"),
        required_by=when("in_hours", "within 72 hrs", amount=72),
    ),
    "o03": extraction(
        products=["kit A"],
        qty_required=q(500, "500"),
        required_by=when("day_after_tomorrow", "by day after tomorrow"),
        min_shelf_life_days=q(45, "min 45 days expiry"),
    ),
    "o04": extraction(
        products=["SK-A"],
        qty_required=q(300, "x 300"),
        required_by=when("tomorrow", "tmrw EOD", time_of_day="end_of_day"),
    ),
    "o05": extraction(
        products=["surgical kits A"],
        qty_required=q(200, "200"),
        priority=prio("Emergency"),
        required_by=when("today", "before 6 pm today", clock_time="18:00"),
        notes="OT",
    ),
    "o06": extraction(
        products=["SK-A"],
        qty_required=q(2000, "2k"),
        required_by=when("date", "by 12th oct", day=12, month=10),
    ),
    "o07": extraction(
        products=["rapid kits"],
        qty_required=q(200, "200"),
        priority=prio("urgent"),
        required_by=when("tomorrow", "tomorrow morning", time_of_day="morning"),
    ),
    "o08": extraction(
        products=["Rapid diagnostic kit"],
        qty_required=q(150, "150 qty"),
        required_by=when("weekday", "by thursday", weekday="thursday"),
        notes="for lab",
    ),
    "o09": extraction(
        products=["rapid diagnostic kits"],
        qty_required=q(250, "250"),
        required_by=when("in_hours", "in 48 hrs", amount=48),
        min_shelf_life_days=q(60, "shelf life atleast 60 days"),
    ),
    "o10": extraction(
        products=["20G cannula"],
        qty_required=q(1500, "1.5k"),
        required_by=when("weekday", "by fri", weekday="friday"),
    ),
    "o11": extraction(
        products=["IV cannula 20 G"],
        qty_required=q(300, "300 pcs"),
        required_by=when("date", "by 10th", day=10),
        notes="ward 4",
    ),
    "o12": extraction(
        products=["IV cannula 20G"],
        qty_required=q(400, "400"),
        required_by=when("tomorrow", "tomorrow"),
    ),
    "o14": extraction(
        products=["kits"], qty_required=q(100, "100"), required_by=when("tomorrow", "by tomorrow")
    ),
    "o15": extraction(products=["kits"], qty_required=q(40, "40"), notes="for casualty"),
    "o16": extraction(
        products=["OT kits"], qty_required=q(60, "60 nos"), required_by=when("tomorrow", "by tmrw")
    ),
    "o17": extraction(
        products=["rapid kits", "IV cannula 20G"],
        required_by=when("weekday", "by fri", weekday="friday"),
    ),
    "o18": extraction(products=["SK-A"], priority=prio("urgently"), notes="for ICU"),
    "o19": extraction(
        qty_required=q(300, "300"),
        required_by=when("tomorrow", "by tomorrow evening", time_of_day="evening"),
    ),
    "o20": extraction(
        products=["rapid diagnostic kits"],
        qty_required=q(250, "250"),
        qty_local_usable=q(50, "50 left"),
        required_by=when("weekday", "by Wed", weekday="wednesday"),
    ),
}
IDEAL["o13"] = IDEAL["o12"]


async def run_case(case: dict[str, Any], raw: dict[str, Any] | None) -> dict[str, Any]:
    result = await chat.draft(
        case["message"],
        user_tz=case["user_tz"],
        now=datetime.fromisoformat(case["now"]),
        user_token=USER_TOKEN,
        hub=FakeHub().reader(),
        provider=FakeProvider(lambda conv: text(""), lambda user_text: raw),
    )
    return chat_evals.as_json(result)


def test_the_set_is_twenty_phrasings_with_a_pass_bar_of_18() -> None:
    cases = chat_evals.load_cases()
    assert [c["id"] for c in cases] == [f"o{i:02}" for i in range(1, 21)]
    assert {c["expect"]["kind"] for c in cases} == {"draft", "clarify", "missing_field"}
    assert chat_evals.PASS_BAR == 18


@pytest.mark.parametrize("case", chat_evals.load_cases(), ids=lambda c: c["id"])
async def test_a_correct_extraction_passes_every_phrasing(case: dict[str, Any]) -> None:
    out = await run_case(case, IDEAL[case["id"]])
    assert chat_evals.grade(case, out, CATALOG) == []


async def test_the_grader_fails_wrong_drafts() -> None:
    (o01,) = [c for c in chat_evals.load_cases() if c["id"] == "o01"]
    wrong_day = {**IDEAL["o01"], "required_by": when("weekday", "by fri", weekday="friday")}
    out = await run_case(o01, wrong_day)
    out["draft"]["required_by"] = "2026-10-10T10:00:00+05:30"
    out["draft"]["qty_required"] = 800
    out["missing_fields"] = []
    failures = chat_evals.grade(o01, out, CATALOG)
    assert any("required_by" in f for f in failures)
    assert any("qty_required" in f for f in failures)
    assert any("not in missing_fields" in f for f in failures)
    assert chat_evals.grade(o01, {"draft": None}, CATALOG) == ["no draft"]


async def test_the_grader_fails_a_silently_picked_product() -> None:
    (o14,) = [c for c in chat_evals.load_cases() if c["id"] == "o14"]
    picked = {**IDEAL["o14"], "products": ["SK-A"]}  # as if "kits" had been read as SK-A
    out = await run_case({**o14, "message": o14["message"] + " SK-A"}, picked)
    failures = chat_evals.grade(o14, out, CATALOG)
    assert "a product was chosen: SURG-KIT-A" in failures
    assert any("not among the candidates" in f for f in failures)


def test_without_a_key_the_runner_skips_clearly(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = Settings(ai_api_key="", _env_file=None)  # type: ignore[call-arg]
    assert chat_evals.main([], cfg) == 0
    assert "Skipping the chat-ordering eval" in capsys.readouterr().out
