import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str = Field(description='e.g. "recommendation.escalated".')
    payload: dict[str, Any] = Field(
        description="recommendation.escalated: recommendation_id, shortage_id, escalated_by, "
        "reason (null if none was typed), expires_at."
    )
    read_at: datetime | None
    created_at: datetime
