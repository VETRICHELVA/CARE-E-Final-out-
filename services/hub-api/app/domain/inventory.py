"""Transferable quantity per batch (business-rules.md §2). Pure functions, no I/O."""

from datetime import date
from typing import Protocol


class Batch(Protocol):
    on_hand: int
    reserved: int
    allocated: int
    safety_stock: int
    quarantined: int
    expiry_date: date


def is_expired(batch: Batch, today: date) -> bool:
    """A batch whose expiry_date is today or earlier counts as fully expired."""
    return batch.expiry_date <= today


def batch_transferable(batch: Batch, today: date, held_qty: int = 0) -> int:
    """max(0, on_hand - reserved - allocated - safety_stock - quarantined), 0 if expired.

    `held_qty` is active holds from other shortages, counted as reserved (S06).
    """
    if held_qty < 0:
        raise ValueError("held_qty cannot be negative.")
    if is_expired(batch, today):
        return 0
    return max(
        0,
        batch.on_hand
        - batch.reserved
        - batch.allocated
        - batch.safety_stock
        - batch.quarantined
        - held_qty,
    )


def days_to_expiry_at(batch: Batch, arrival_date: date) -> int:
    """(expiry_date - arrival_date).days; the shelf-life gate compares it to the minimum."""
    return (batch.expiry_date - arrival_date).days
