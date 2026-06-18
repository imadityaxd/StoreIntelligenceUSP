from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EventType(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class SessionStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    PROCESSING = "processing"
    REPLAYING = "replaying"
    COMPLETE = "complete"
    FAILED = "failed"
    STOPPED = "stopped"
    STOPPING = "stopping"


class EventMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    queue_depth: int | None = Field(default=None, ge=0)
    sku_zone: str | None = None
    session_seq: int = Field(ge=1)


class StoreEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    session_id: str | None = Field(default=None, min_length=1)
    store_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    visitor_id: str = Field(min_length=1)
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = Field(ge=0)
    is_staff: bool
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_event_shape(self) -> StoreEvent:
        instant_events = {
            EventType.ENTRY,
            EventType.EXIT,
            EventType.ZONE_ENTER,
            EventType.ZONE_EXIT,
            EventType.BILLING_QUEUE_JOIN,
            EventType.REENTRY,
        }
        zone_events = {
            EventType.ZONE_ENTER,
            EventType.ZONE_EXIT,
            EventType.ZONE_DWELL,
            EventType.BILLING_QUEUE_JOIN,
            EventType.BILLING_QUEUE_ABANDON,
        }

        if self.event_type in instant_events and self.dwell_ms != 0:
            raise ValueError(f"{self.event_type} must have dwell_ms=0")

        if self.event_type == EventType.ZONE_DWELL and self.dwell_ms < 30_000:
            raise ValueError("ZONE_DWELL must represent at least 30 seconds")

        if self.event_type in zone_events and not self.zone_id:
            raise ValueError(f"{self.event_type} requires zone_id")

        if self.event_type in {EventType.ENTRY, EventType.EXIT, EventType.REENTRY} and self.zone_id:
            raise ValueError(f"{self.event_type} should not include zone_id")

        if self.event_type == EventType.BILLING_QUEUE_JOIN:
            if self.metadata.queue_depth is None or self.metadata.queue_depth <= 0:
                raise ValueError("BILLING_QUEUE_JOIN requires metadata.queue_depth > 0")

        return self

    def as_storage_row(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["event_type"] = self.event_type.value
        return payload


class StoreSessionCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str | None = Field(default=None, min_length=1)
    store_id: str = Field(min_length=1)
    camera_id: str | None = None
    source_id: str | None = None
    source_type: str | None = None
    source_label: str | None = None
    video_path: str | None = None
    status: SessionStatus = SessionStatus.CREATED
    analysis_start: datetime | None = None
    analysis_end: datetime | None = None
    model_version: str | None = None
    calibration_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("analysis_start", "analysis_end")
    @classmethod
    def optional_timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class StoreSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SessionStatus | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    analysis_start: datetime | None = None
    analysis_end: datetime | None = None
    event_count: int | None = Field(default=None, ge=0)
    error: str | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("started_at", "completed_at", "analysis_start", "analysis_end")
    @classmethod
    def optional_update_timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class PosTransactionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    transaction_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    timestamp: datetime
    basket_value_inr: float = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def pos_timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class PosImportPayload(BaseModel):
    transactions: list[PosTransactionPayload] = Field(default_factory=list)


class VisitorStaffCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, min_length=1)
    is_staff: bool
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
