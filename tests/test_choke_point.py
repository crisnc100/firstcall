"""Only firstcall/outbound/ can send anything (F27, the source-tree half)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "firstcall"
SENDERS = {
    "smtplib",
    "pywebpush",
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "http.client",
    "urllib.request",
    "py_vapid",
    "http_ece",
}
OUTBOUND = PACKAGE / "outbound"
# The model read posts intake text to the model provider. It addresses no person, and its
# key, budget and kill switch are its own guard (PR1b), so it is the one other module allowed
# an HTTP client.
MODEL_PROVIDER = PACKAGE / "model" / "anthropic.py"


def _sends(name: str) -> bool:
    return name in SENDERS or any(name.startswith(f"{s}.") for s in SENDERS)


def sending_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if _sends(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _sends(node.module):
                found.append(node.module)
            else:
                full = (f"{node.module}.{alias.name}" for alias in node.names)
                found.extend(name for name in full if _sends(name))
    return found


def test_no_module_outside_outbound_imports_a_sender() -> None:
    files = sorted(PACKAGE.rglob("*.py"))
    assert any(OUTBOUND in f.parents for f in files)
    hits = {
        str(f.relative_to(ROOT)): found
        for f in files
        if OUTBOUND not in f.parents
        and f != MODEL_PROVIDER
        and (found := sending_imports(f.read_text()))
    }
    assert hits == {}


def test_the_outbound_package_really_holds_the_senders() -> None:
    inside = {name for f in OUTBOUND.rglob("*.py") for name in sending_imports(f.read_text())}
    assert {"smtplib", "pywebpush", "requests"} <= inside


def test_the_scan_sees_each_import_form() -> None:
    assert sending_imports("import smtplib") == ["smtplib"]
    assert sending_imports("from urllib import request") == ["urllib.request"]
    assert sending_imports("import http.client as h") == ["http.client"]
    assert sending_imports("from pywebpush import webpush") == ["pywebpush"]
    assert sending_imports("from httpx import Client") == ["httpx"]
    assert sending_imports("import urllib.parse\nimport socket") == []
