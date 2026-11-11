"""Firstcall shares nothing with the private tool that inspired it.

The scan hashes every word in committed text and compares it with a hash list, so the
names it guards against never appear in this repo, not even here.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Word hashes, not secrets.
WORD_HASHES = (
    "78242caade0d98605142646c4d4e686bd996e3316d5425ded1f798a60632bef0",  # pragma: allowlist secret
    "0ff614693e4319a9493ea82d2fe1c244ec451163a105d0943fad73816fe5905a",  # pragma: allowlist secret
    "45a784969241aad961edd982384eb77eff4c1c905c101660966b38b33c1283b7",  # pragma: allowlist secret
)
FORBIDDEN_SHA256 = frozenset(WORD_HASHES)
WORD = re.compile(r"[a-z0-9]+\+?")
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".jsonl",
    ".ts",
    ".html",
    ".sh",
    ".ini",
    ".mako",
    ".txt",
    ".cfg",
    "",
}


def forbidden_words(text: str) -> set[str]:
    return {
        w
        for w in WORD.findall(text.lower())
        if hashlib.sha256(w.encode()).hexdigest() in FORBIDDEN_SHA256
    }


def repo_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [ROOT / p for p in listed if Path(p).suffix in TEXT_SUFFIXES and (ROOT / p).is_file()]


def test_no_committed_text_names_the_inspiring_tool() -> None:
    files = repo_files()
    assert any(p.name == "plan.md" for p in files)
    hits = {
        str(p.relative_to(ROOT)): found
        for p in files
        if (found := forbidden_words(p.read_text(errors="replace")))
    }
    assert hits == {}


def test_the_scan_sees_a_listed_word() -> None:
    probe = "".join(chr(c) for c in (75, 121, 108, 105, 101))
    assert forbidden_words(f"see {probe} here") == {probe.lower()}
    assert forbidden_words("Firstcall leads") == set()
