"""Repo-wide test helper.

Every service names its package `app`, so importing two services' modules in
one pytest run collides on that name. Load each under a unique alias instead,
as a real package so relative imports (`from . import dateparse`) resolve.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_service_module(alias: str, relpath: str):
    """Import services/<svc>/app/<mod>.py as `alias`, isolated from other services."""
    if alias in sys.modules:
        return sys.modules[alias]
    path = ROOT / relpath
    pkg_dir = path.parent
    pkg_alias = f"{alias}__pkg"

    # Register the containing directory as a package so intra-package
    # relative imports inside the service keep working.
    pkg_spec = importlib.util.spec_from_file_location(
        pkg_alias, pkg_dir / "__init__.py",
        submodule_search_locations=[str(pkg_dir)])
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_alias] = pkg
    pkg_spec.loader.exec_module(pkg)

    full = f"{pkg_alias}.{path.stem}"
    spec = importlib.util.spec_from_file_location(full, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module
