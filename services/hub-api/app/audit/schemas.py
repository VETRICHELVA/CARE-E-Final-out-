import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID | None
    org_id: uuid.UUID
    entity: str
    entity_id: uuid.UUID
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str
    reason_source: str
    ts: datetime
