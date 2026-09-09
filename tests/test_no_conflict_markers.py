"""No merge conflict can reach production through a file nothing validates.

WHY THIS FILE EXISTS: on 2026-09-09 a merge left an unresolved conflict in
`gateway/static/home.css` — markers and both sides still in the file — and the
FULL SUITE PASSED. 7,854 tests green with a broken stylesheet on disk.

`scripts/test.sh` runs `node --check` over every .js and `bash -n` over every
.sh, so a conflict in those is caught immediately. CSS and HTML have no such
check, and CSS fails silently by design: a browser discards rules it cannot
parse and renders the rest, so the page comes up looking almost right. Several
agents work this repo at once and every one of them merges production before
promoting, which makes this a routine event rather than a freak one.

Two checks, because they fail differently: markers are the accident, and
unbalanced braces are what a half-resolved conflict leaves behind after
someone deletes the markers but not the duplicated block.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Built at runtime so this file does not match its own test.
OURS = "<" * 7
THEIRS = ">" * 7
SPLIT = "=" * 7

TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".sh", ".yml", ".yaml",
                 ".json", ".txt", ".example"}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout.split()
    return [ROOT / f for f in out
            if (ROOT / f).suffix in TEXT_SUFFIXES and (ROOT / f).is_file()]


def test_there_are_files_to_check():
    """A scan that silently matches nothing passes forever."""
    files = tracked_files()
    assert len(files) > 50, f"only found {len(files)} files to scan"
    assert any(f.suffix == ".css" for f in files), "no CSS in range"
    assert any(f.suffix == ".html" for f in files), "no HTML in range"


@pytest.mark.parametrize("path", tracked_files(), ids=lambda p: str(p.name))
def test_no_unresolved_conflict_markers(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    for line_no, line in enumerate(text.splitlines(), 1):
        for marker in (OURS + " ", THEIRS + " "):
            assert not line.startswith(marker), (
                f"{path.relative_to(ROOT)}:{line_no} still has a conflict marker")
        assert line.rstrip() != SPLIT, (
            f"{path.relative_to(ROOT)}:{line_no} still has a conflict separator")


@pytest.mark.parametrize(
    "path", [p for p in tracked_files() if p.suffix == ".css"],
    ids=lambda p: str(p.name))
def test_stylesheets_have_balanced_braces(path):
    """What a half-resolved conflict leaves once the markers are deleted but a
    duplicated block is not. A browser drops the unparseable rules and renders
    the rest, so it looks nearly right and reports nothing."""
    text = path.read_text(encoding="utf-8")
    # Strip comments first: a `{` inside one is not a block.
    stripped, i, out = text, 0, []
    while i < len(stripped):
        if stripped.startswith("/*", i):
            end = stripped.find("*/", i + 2)
            i = len(stripped) if end == -1 else end + 2
            continue
        out.append(stripped[i])
        i += 1
    body = "".join(out)
    opens, closes = body.count("{"), body.count("}")
    assert opens == closes, (
        f"{path.relative_to(ROOT)} has {opens} '{{' and {closes} '}}' — "
        f"unbalanced, so a browser will silently drop rules")
