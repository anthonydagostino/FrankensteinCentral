"""Every name a service calls must actually exist.

WHY THIS FILE EXISTS. On 2026-09-16 the dashboard went down, and the cause was
one missing word. `services/assistant/app/main.py` gained

    "amex": amex_brief(amex_home),

and the `from .dashboard import (...)` line above it was never updated. The
function existed, was fully tested, and was imported nowhere. Every call to
`build_home()` — the assistant's only real endpoint, the one the whole home
screen is — raised NameError, so the page had no data at all.

8,402 tests passed on that commit. Not one of them called `build_home()`,
because it fans out to twenty services over HTTP; the suite tests the pure
functions in `dashboard.py` directly and never walks the module that wires
them together. A NameError at the bottom of a 1,100-line file is invisible to
an import check too: Python resolves globals at CALL time, so the module
imports perfectly and fails only when someone loads the dashboard.

WHAT THIS DOES. Parses every shipped service module and asserts that each bare
name it calls is bound somewhere it could be found at runtime — a module-level
import or def, a nested def, a parameter, an assignment, or a builtin. That is
the cheapest possible check for the most expensive kind of typo.

WHAT THIS CANNOT PROVE. It only sees calls through a plain name: `foo()`, not
`mod.foo()` or `getattr(mod, name)()`. It says nothing about arguments, types,
or whether the function does the right thing. It is a spell-checker for calls,
which is exactly the defect it was written for — and the reason to keep it
narrow is that a broad version would need a type checker and would not have
caught this any faster.
"""
import ast
import builtins
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# `dir(builtins)`, not `dir(__builtins__)`: the latter is a module in some
# import contexts and a plain dict in others, and under pytest it came back as
# the dict — so every call to `int` or `sorted` was reported as unbound. A
# guard whose first run is thirty-two false positives teaches you to ignore it.
BUILTINS = set(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__",
    # Names that only exist while an exception is being handled, and a couple
    # of typing-time constructs ast sees as calls.
    "reveal_type",
}


def modules():
    for base in (ROOT / "services", ROOT / "gateway" / "app"):
        for path in sorted(base.rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            yield path


def _bound_names(tree):
    """Everything a runtime lookup inside this module could find.

    Deliberately generous: a name bound ANYWHERE in the module counts, even if
    it is bound in a different function. Narrowing that would need real scope
    analysis and would start reporting names that resolve fine, and a guard
    that cries wolf gets deleted. Being generous is what keeps this honest —
    anything it reports is genuinely unbound everywhere.
    """
    bound = set(BUILTINS)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            args = getattr(node, "args", None)
            if args is not None:
                for arg in (args.posonlyargs + args.args + args.kwonlyargs):
                    bound.add(arg.arg)
                for extra in (args.vararg, args.kwarg):
                    if extra is not None:
                        bound.add(extra.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Lambda):
            for arg in node.args.args:
                bound.add(arg.arg)
        elif isinstance(node, ast.Global):
            bound.update(node.names)
    return bound


def _called_names(tree):
    return {n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


MODULES = list(modules())


def test_the_scan_found_the_services():
    """A glob that matches nothing passes every assertion below forever."""
    names = {p.name for p in MODULES}
    assert len(MODULES) >= 15, f"only found {len(MODULES)} modules"
    assert "main.py" in names
    assert any("assistant" in p.parts for p in MODULES), "the assistant went unread"


@pytest.mark.parametrize(
    "path", MODULES, ids=[str(p.relative_to(ROOT)) for p in MODULES])
def test_every_called_name_exists(path):
    tree = ast.parse(path.read_text())
    missing = sorted(_called_names(tree) - _bound_names(tree))
    assert missing == [], (
        f"{path.relative_to(ROOT)} calls {missing}, which is bound nowhere in "
        "the module. Python resolves this at CALL time, so the module imports "
        "fine and raises NameError only when the code path runs — which is "
        "how one missing import took the dashboard down on 2026-09-16.")


def test_no_two_test_files_share_a_basename():
    """Two `test_service.py` files break collection for the ENTIRE suite.

    pytest imports test modules by basename when the directory has no
    `__init__.py`, so a second `test_service.py` anywhere in the repo does not
    fail its own tests — it aborts collection with `import file mismatch` and
    exit code 2, which `scripts/deploy.sh` reads as a red suite. The whole
    deploy stops for a filename.

    It lives in this file because it is the same species as the rest: a defect
    that no amount of testing the CODE can find, because it is about the
    arrangement of the files around it.
    """
    from collections import defaultdict
    seen = defaultdict(list)
    for path in ROOT.rglob("test_*.py"):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        seen[path.name].append(str(path.relative_to(ROOT)))
    clashes = {name: paths for name, paths in seen.items() if len(paths) > 1}
    assert clashes == {}, (
        f"test files sharing a basename: {clashes}. pytest imports by basename "
        "and aborts collection for the whole suite — rename one, e.g. "
        "test_weather_service.py rather than a second test_service.py.")
