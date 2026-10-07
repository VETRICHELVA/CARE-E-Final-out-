"""Posting batches to the hub's POST /internal/telemetry with the ingest token."""

import logging
from collections.abc import Sequence
from enum import Enum

import httpx

from app.telemetry import Reading

log = logging.getLogger("iot_ingest")


class Outcome(Enum):
    SENT = "sent"  # the hub took the batch (new readings stored, repeats ignored)
    RETRY = "retry"  # hub unreachable, overloaded or refusing the token: keep and resend
    REJECTED = "rejected"  # the hub will never take this batch (e.g. 422): drop it


class HubClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
            transport=transport,
        )

    def post(self, readings: Sequence[Reading]) -> Outcome:
        body = {"readings": [r.to_hub() for r in readings]}
        try:
            response = self._http.post("/internal/telemetry", json=body)
        except httpx.HTTPError as e:
            log.warning("hub unreachable, will retry %d readings: %s", len(readings), e)
            return Outcome.RETRY
        if response.is_success:
            result = response.json()
            log.info(
                "posted %d readings: %s stored, %s duplicates",
                len(readings),
                result.get("stored"),
                result.get("duplicates"),
            )
            if result.get("unknown_devices"):
                log.warning("hub has no Device for %s", ", ".join(result["unknown_devices"]))
            return Outcome.SENT
        if response.status_code in (401, 403, 429) or response.status_code >= 500:
            log.error("hub returned %d, will retry: %s", response.status_code, response.text)
            return Outcome.RETRY
        log.error(
            "hub rejected %d readings (%d): %s", len(readings), response.status_code, response.text
        )
        return Outcome.REJECTED

    def close(self) -> None:
        self._http.close()
