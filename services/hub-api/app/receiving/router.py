import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import Capability
from app.auth.deps import require
from app.auth.models import User
from app.db import SessionDep
from app.receiving import service
from app.receiving.models import Receipt
from app.receiving.schemas import ReceiptIn, ReceiptOut, ReconciliationOut

router = APIRouter(tags=["receiving"])

# api-and-events.md (S12).
Receiver = Annotated[User, Depends(require(Capability.RECEIPT_RECORD))]


async def receipt_out(session: AsyncSession, receipt: Receipt) -> ReceiptOut:
    rec = await service.reconciliation_for(session, receipt.shipment_id)
    out = ReceiptOut.model_validate(receipt)
    out.reconciliation = ReconciliationOut.model_validate(rec) if rec else None
    return out


@router.post("/shipments/{shipment_id}/receipt", status_code=201)
async def record_receipt(
    shipment_id: uuid.UUID, body: ReceiptIn, user: Receiver, session: SessionDep
) -> ReceiptOut:
    """The receiving org records what arrived (business-rules.md §9): DELIVERED ->
    RECONCILED, the accepted stock becomes a new batch in its inventory, and once every
    shipment of the shortage has a receipt the shortage is reconciled (RESOLVED, or
    PARTIALLY_RESOLVED with a residual shortage that starts matching). 403 for any other
    org; 409 unless DELIVERED; 400 if received > expected, accepted + rejected ≠ received,
    a shipment with an excursion on record has no inspection note, or accepted stock has
    no expiry."""
    receipt = await service.record(session, user, shipment_id, body)
    out = await receipt_out(session, receipt)
    await session.commit()
    return out
