"""The front page is served at "/", the app at every other page address, and neither in
place of an API answer."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firstcall.api import serve_pages


def test_page_addresses_get_index_files_get_themselves_and_api_paths_404(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>front</title>")
    (tmp_path / "app.html").write_text("<!doctype html><title>app</title>")
    (tmp_path / "sw.js").write_text("// worker")
    (tmp_path.parent / "secret.txt").write_text("outside")
    app = FastAPI()
    serve_pages(app, tmp_path)
    client = TestClient(app)
    front = client.get("/")
    assert front.status_code == 200
    assert "<title>front</title>" in front.text
    assert "<title>app</title>" not in front.text
    assert front.headers["cache-control"] == "no-cache"
    for path in ("/pool", "/leads/123", "/admin/users", "/mine", "/signin"):
        answer = client.get(path)
        assert answer.status_code == 200
        assert "<title>app</title>" in answer.text
        assert "<title>front</title>" not in answer.text
        assert answer.headers["cache-control"] == "no-cache"
    assert client.get("/sw.js").text == "// worker"
    assert "outside" not in client.get("/..%2Fsecret.txt").text
    for path in ("/api/nothing", "/intake/x", "/healthz/x"):
        assert client.get(path).status_code == 404
