"""Repo-wide test helper.

Every service names its package `app`, so importing two services' modules in
one pytest run collides on that name. Load each by file path under a unique
module name instead, which also keeps services importable without installing
them.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_service_module(alias: str, relpath: str):
    """Import services/<...>.py as `alias`, isolated from other services."""
    if alias in sys.modules:
        return sys.modules[alias]
    path = ROOT / relpath
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module
