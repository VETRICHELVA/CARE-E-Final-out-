"""The eval set and its grader (the real-model run itself needs a key and a live hub)."""

import json

import pytest
from app import evals
from app.config import Settings


def test_the_eval_set_is_fifteen_cases_over_scenario_1() -> None:
    cases = evals.load_cases()
    assert [c["id"] for c in cases] == [f"c{i:02}" for i in range(1, 16)]
    assert {c["after_step"] for c in cases} == {2, 4, 7}
    for case in cases:
        assert case["as_user"] in ("approver@hospital-a.demo", "approver@hospital-b.demo")
        assert all(v.startswith("$s1.") for v in case["context"].values())
        assert case["must_match"] or case["must_not_match"]


def test_why_d_needs_12_days_and_30() -> None:
    (c01,) = [c for c in evals.load_cases() if c["id"] == "c01"]
    results = [{"gate_results": [{"reason": "Expires in 12 days; 30 required"}]}]
    assert (
        evals.grade(c01, "Hospital D's stock expires in 12 days; 30 are required.", results) == []
    )
    failures = evals.grade(c01, "Its stock expires too soon.", results)
    assert len(failures) == 2


def test_any_number_missing_from_the_tool_results_fails_the_case() -> None:
    (c04,) = [c for c in evals.load_cases() if c["id"] == "c04"]
    results = [{"qty_required": 1000, "qty_local_usable": 150, "shortfall": 850}]
    ok = "You need 1,000, have 150 usable, so the shortfall is 850."
    assert evals.grade(c04, ok, results) == []
    bad = evals.grade(c04, ok + " That is 85 percent.", results)
    assert bad == ["numbers not in any tool result: 85"]


def test_must_not_match_and_another_orgs_shortage() -> None:
    (c15,) = [c for c in evals.load_cases() if c["id"] == "c15"]
    assert evals.grade(c15, "That information is not available to you.", []) == []
    leaked = evals.grade(c15, "Not available; it expires in 12 days.", [{"days": 12}])
    assert leaked == ["must not match '\\\\b(12|30|850|900|1000)\\\\b'"]


def test_refs_resolve_per_step() -> None:
    refs = {"shortage": "s-id"}
    assert evals.resolve({"shortage_id": "$s1.shortage"}, refs) == {"shortage_id": "s-id"}
    with pytest.raises(KeyError):
        evals.resolve({"shipment_id": "$s1.shipment"}, refs)


def test_without_a_key_the_runner_skips_clearly(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = Settings(ai_api_key="", _env_file=None)  # type: ignore[call-arg]
    assert evals.main([], cfg) == 0
    assert "Skipping the copilot eval" in capsys.readouterr().out


def test_the_chat_eval_names_products_by_catalog_code() -> None:
    codes = {"SURG-KIT-A", "DIAG-RDK", "IV-CAN-20G"}
    lines = (evals.SERVICE_DIR / "evals" / "chat_orders.jsonl").read_text().splitlines()
    for line in filter(None, lines):
        expect = json.loads(line)["expect"]
        if "product" in expect:
            assert expect["product"] in codes
        assert set(expect.get("product_candidates_include", [])) <= codes
