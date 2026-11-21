"""Rendering the voice gold swaps the seed folder in whole, or leaves it as it was."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from firstcall.corpus import voice
from firstcall.corpus.voice import (
    VOICE_SEED,
    read_voice_manifest,
    render_voice,
    write_voice_manifest,
)


def _gold(root: Path) -> Path:
    folder = root / f"seed-{VOICE_SEED}"
    (folder / "clean").mkdir(parents=True)
    (folder / "clean" / "stale.mp3").write_bytes(b"old clip")
    (folder / voice.SPOKEN).write_text("old spoken\n")
    (root / "LICENSES.md").write_text("licenses\n")
    write_voice_manifest(root)
    return folder


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file()}  # fmt: skip


def _fake_render(fail_at: int | None = None) -> Any:
    made: list[str] = []

    def render_clip(piper: Any, models: Any, clip: dict[str, Any], text: str, root: Path) -> None:
        if len(made) == fail_at:
            raise RuntimeError("piper stopped")
        target = root / clip["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
        made.append(clip["file"])

    return render_clip


def test_a_failed_render_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gold(tmp_path)
    before = _files(tmp_path)
    monkeypatch.setattr(voice, "render_clip", _fake_render(fail_at=40))
    monkeypatch.setattr(voice, "seconds", lambda path: 1.0)
    with pytest.raises(RuntimeError, match="piper stopped"):
        render_voice(["piper"], tmp_path / "models", root=tmp_path)
    assert _files(tmp_path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "LICENSES.md", "MANIFEST.json", f"seed-{VOICE_SEED}",
    ]  # fmt: skip


def test_a_finished_render_replaces_the_seed_folder_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _gold(tmp_path)
    monkeypatch.setattr(voice, "render_clip", _fake_render())
    monkeypatch.setattr(voice, "seconds", lambda path: 1.0)
    hashes = render_voice(["piper"], tmp_path / "models", root=tmp_path)
    clips = [json.loads(line) for line in (folder / voice.CLIPS).read_text().splitlines()]
    assert not (folder / "clean" / "stale.mp3").exists()
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.mp3")) == sorted(
        clip["file"] for clip in clips
    )
    assert (folder / voice.SPOKEN).read_text() != "old spoken\n"
    assert hashes == read_voice_manifest(tmp_path / "MANIFEST.json")
    assert hashes == voice.voice_file_hashes(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "LICENSES.md", "MANIFEST.json", f"seed-{VOICE_SEED}",
    ]  # fmt: skip
