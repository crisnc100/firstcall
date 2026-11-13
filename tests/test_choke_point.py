"""Only firstcall/outbound/ can send anything (F27, the source-tree half)."""

from __future__ import annotations

import ast
from pathlib import Path
from urllib.parse import urlsplit

from firstcall.model import anthropic

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
# The model read posts intake text to the model provider. It addresses no person, so it is
# the one other module allowed an HTTP client; a test pins its only target to the model API.
MODEL_PROVIDER = PACKAGE / "model" / "anthropic.py"
MODEL_HOST = "api.anthropic.com"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request", "stream", "send", "head"}


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


def _http_receiver(node: ast.expr) -> bool:
    """httpx itself, or a name or attribute that names a client, such as self._client."""
    if isinstance(node, ast.Name):
        return node.id == "httpx" or "client" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "client" in node.attr.lower()
    return False


def model_provider_targets(source: str) -> tuple[set[str], int, list[str]]:
    """Every URL literal in the module, and every HTTP call, on httpx or on a client object,
    whose target is not the API_URL constant."""
    tree = ast.parse(source)
    urls = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "://" in node.value
    }
    stray: list[str] = []
    checked = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in HTTP_METHODS or not _http_receiver(node.func.value):
            continue
        checked += 1
        first = node.args[0] if node.args else None
        if not (isinstance(first, ast.Name) and first.id == "API_URL"):
            stray.append(f"{node.func.attr} at line {node.lineno}")
    return urls, checked, stray


def test_the_model_provider_module_calls_only_the_model_api() -> None:
    urls, checked, stray = model_provider_targets(MODEL_PROVIDER.read_text())
    assert urls == {anthropic.API_URL}
    assert checked >= 1
    assert urlsplit(anthropic.API_URL).scheme == "https"
    assert urlsplit(anthropic.API_URL).hostname == MODEL_HOST
    assert stray == []


def test_the_model_provider_check_catches_another_target() -> None:
    urls, checked, stray = model_provider_targets(
        'API_URL = "https://api.anthropic.com/v1/messages"\n'
        "client.post(API_URL)\n"
        'client.post("https://hooks.example/x")\n'
        "httpx.get(user.email)\n"
        'data.get("content")\n'
    )
    assert urls == {"https://api.anthropic.com/v1/messages", "https://hooks.example/x"}
    assert checked == 3
    assert stray == ["post at line 3", "get at line 4"]


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
