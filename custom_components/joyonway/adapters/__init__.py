"""Adapter registry for Joyonway spa controllers."""
from __future__ import annotations

from .base import ModelAdapter, SpaEntityDescription
from .p25b85 import P25B85Adapter

# Registry of available model adapters
ADAPTERS: dict[str, type] = {
    "P25B85": P25B85Adapter,
}


def get_adapter(model: str) -> ModelAdapter:
    """Get an adapter instance by model name."""
    adapter_class = ADAPTERS.get(model)
    if adapter_class is None:
        raise ValueError(f"Unknown model: {model}. Available: {list(ADAPTERS.keys())}")
    return adapter_class()


__all__ = ["ModelAdapter", "SpaEntityDescription", "get_adapter", "ADAPTERS"]

