"""Small test helper for loading modules from file paths.

Used to test integration submodules without importing Home Assistant package
glue from ``custom_components.joyonway.__init__``.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def load_module(name: str, path: Path):
    """Load a module from ``path`` under the provided ``name``."""
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to create import spec for {name} at {path}")
    mod = module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
