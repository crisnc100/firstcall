"""The built pages are served for every page address, and never in place of an API answer."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firstcall.api import serve_pages


def test_page_addresses_get_index_files_get_themselves_and_api_paths_404(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")
    (tmp_path / "sw.js").write_text("// worker")
    (tmp_path.parent / "secret.txt").write_text("outside")
    app = FastAPI()
    serve_pages(app, tmp_path)
    client = TestClient(app)
    for path in ("/", "/pool", "/leads/123", "/admin/users"):
        answer = client.get(path)
        assert answer.status_code == 200
        assert "<title>app</title>" in answer.text
        assert answer.headers["cache-control"] == "no-cache"
    assert client.get("/sw.js").text == "// worker"
    assert "outside" not in client.get("/..%2Fsecret.txt").text
    for path in ("/api/nothing", "/intake/x", "/healthz/x"):
        assert client.get(path).status_code == 404
