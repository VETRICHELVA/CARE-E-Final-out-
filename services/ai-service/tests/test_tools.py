"""The tools are the spec's seven, read-only, and the hub client cannot write."""

import inspect
from typing import Any

import pytest
from app import tools
from app.hub import HubReader
from app.numbers import numbers_in_data, numbers_in_text, unsupported_numbers


def test_the_tools_are_the_specs_seven_reads() -> None:
    assert [t["name"] for t in tools.TOOLS] == [
        "get_shortage",
        "get_match_run",
        "get_candidate",
        "get_recommendation",
        "get_shipment",
        "get_coldchain_events",
        "get_audit",
    ]
    for t in tools.TOOLS:
        schema = t["input_schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_the_hub_client_only_reads() -> None:
    public = {n for n, _ in inspect.getmembers(HubReader, inspect.isfunction) if n[0] != "_"}
    assert public == {"get", "aclose"}
    source = inspect.getsource(HubReader)
    for verb in ("post(", "put(", "patch(", "delete(", ".request("):
        assert verb not in source


def test_display_adds_rupees_and_rounds_floats() -> None:
    hub: dict[str, Any] = {
        "landed_cost_paise": 2400000,
        "unit_price_paise": None,
        "eta_hours": 5.833333,
        "lines": [{"unit_price_paise": 2800, "ok": True}],
    }
    assert tools.display(hub) == {
        "landed_cost_paise": 2400000,
        "landed_cost_rupees": "24000.00",
        "unit_price_paise": None,
        "eta_hours": 5.83,
        "lines": [{"unit_price_paise": 2800, "unit_price_rupees": "28.00", "ok": True}],
    }


def test_numbers_are_compared_by_value() -> None:
    assert numbers_in_text("1,000 units; 12 days; 30 required; 5.50 h; 09:05") == [
        "1000",
        "12",
        "30",
        "5.5",
        "09:05",
    ]
    data = {
        "reason": "Expires in 12 days; 30 required",
        "qty": 1000.0,
        "id": "3f2a9c1e-1111-2222-3333-444455556666",
        "required_by": "2026-10-09T06:00:00Z",
        "code": "IV-CAN-20G",
    }
    found = numbers_in_data(data)
    assert {"12", "30", "1000", "2026-10-09", "10-09", "06:00", "20"} <= found
    assert "1111" not in found and "3" not in found  # UUID digits are no figures
    # A timestamp allows its date and time as wholes, never its parts as quantities.
    assert not {"2026", "10", "9", "6", "0"} & found


@pytest.mark.parametrize(
    ("answer", "missing"),
    [
        ("Hospital D expires in 12 days; 30 are required.", []),
        ("It holds 1,000 units.", []),
        ("Hospital D expires in 11 days.", ["11"]),
        ("About 6 hours, roughly 24,000 rupees.", ["6", "24000"]),
        ("Match run #3 rejected it.", ["3"]),
    ],
)
def test_unsupported_numbers(answer: str, missing: list[str]) -> None:
    results = [{"reason": "Expires in 12 days; 30 required", "transferable_qty": 1000}]
    assert unsupported_numbers(answer, results) == missing


@pytest.mark.parametrize(
    ("answer", "data", "missing"),
    [
        # From the S13 review: each of these used to pass.
        ("It dropped to -3 °C.", {"min_temp_c": 3.0}, ["-3"]),
        (
            "Apollo can transfer 12 vials.",
            {"ts": "2026-10-08T06:12:40Z", "transferable_qty": 120},
            ["12"],
        ),
        ("Apollo can transfer twelve hundred vials.", {"transferable_qty": 120}, ["1200"]),
        # Dates and times as the tools return them still pass.
        ("Required by 9 Oct at 06:00 UTC.", {"required_by": "2026-10-09T06:00:00Z"}, []),
        ("Required by 2026-10-09.", {"required_by": "2026-10-09T06:00:00Z"}, []),
        ("Required by 10 Oct.", {"required_by": "2026-10-09T06:00:00Z"}, ["10-10"]),
        ("It reached -3 °C, the limit is 8.", {"min_temp_c": -3, "max": 8}, []),
        ("One source covers it: one hundred and twenty units.", {"qty": 120}, []),
        ("IV-CAN-20G, range 3-5.", {"code": "IV-CAN-20G", "range": [3, 5]}, []),
    ],
)
def test_the_number_check_reads_signs_dates_and_words(
    answer: str, data: dict[str, object], missing: list[str]
) -> None:
    assert unsupported_numbers(answer, [data]) == missing
