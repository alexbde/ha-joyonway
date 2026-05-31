"""Base model adapter interface for Joyonway spa controllers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpaEntityDescription:
    """Describes an entity exposed by a model adapter."""

    platform: str  # "sensor" or "binary_sensor"
    key: str  # e.g. "water_temperature"
    name: str  # user-facing name
    icon: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    native_unit: str | None = None
    entity_category: str | None = None  # "diagnostic" or None
    enabled_by_default: bool = True


class ModelAdapter(Protocol):
    """Per-model byte mapping and feature support.

    Each controller model implements this to define:
    - How to identify its broadcast frames
    - How to parse status from a logical (unescaped) frame
    - Which entities to expose in Home Assistant
    """

    model: str
    broadcast_signature: bytes
    unescape_full_frame: bool
    supports_writes: bool

    def parse_status(self, frame: bytes) -> dict | None:
        """Extract state dict from an unescaped broadcast frame.

        Returns None if the frame doesn't match this model's signature.
        """
        ...

    def entity_descriptions(self) -> list[SpaEntityDescription]:
        """Return the list of entities this model exposes."""
        ...

