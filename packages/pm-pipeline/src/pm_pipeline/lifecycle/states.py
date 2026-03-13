"""Market lifecycle state machine."""

from enum import StrEnum


class MarketState(StrEnum):
    DETECTED = "detected"
    ACTIVE = "active"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
    VOIDED = "voided"
    ARCHIVED = "archived"
