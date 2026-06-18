"""Adapter registry for Joyonway spa controllers."""

from __future__ import annotations

from .base import ModelAdapter, SpaEntityDescription
from .p20 import P20B29Adapter
from .p23 import P23B32Adapter
from .p25 import P25B37Adapter, P25B85Adapter

# Registry of available model adapters
ADAPTERS: dict[str, type] = {
    "P20B29": P20B29Adapter,
    "P23B32": P23B32Adapter,
    "P25B37": P25B37Adapter,
    "P25B85": P25B85Adapter,
}


def get_adapter(model: str) -> ModelAdapter:
    """Get an adapter instance by model name."""
    adapter_class = ADAPTERS.get(model)
    if adapter_class is None:
        raise ValueError(f"Unknown model: {model}. Available: {list(ADAPTERS.keys())}")
    return adapter_class()


__all__ = ["ModelAdapter", "SpaEntityDescription", "get_adapter", "ADAPTERS"]
