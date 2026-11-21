"""Speech to text for voice notes, with no key and nothing sent off the machine (ADR-005).

Two modes:
- `local` runs faster-whisper on the CPU with ADR-005's fixed settings. The model loads when
  the transcriber starts, downloading from Hugging Face at a pinned revision into the models
  folder if it is not there yet, and stays loaded. faster-whisper is imported only here, so
  the app image never needs it.
- `replay` looks the transcript up by the audio's SHA-256 in a committed file, so tests and
  the page test need no model. A miss raises.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from firstcall.board.notes import NOTE_MAX
from firstcall.voice.audio import MAX_SECONDS, UndecodableError, decode, seconds

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

    from firstcall.settings import Settings

# Systran's CTranslate2 builds of the candidates, each pinned to one revision so a download is
# always the same model.
WHISPER_REVISIONS = {
    "tiny.en": "0d3d19a32d3338f10357c0889762bd8d64bbdeba",  # pragma: allowlist secret
    "base.en": "3d3d5dee26484f91867d81cb899cfcf72b96be6c",  # pragma: allowlist secret
    "small.en": "d1d751a5f8271d482d14ca55d9e2deeebbae577f",  # pragma: allowlist secret
}
COMPUTE_TYPE = "int8"
CPU_THREADS = 2
LANGUAGE = "en"
BEAM_SIZE = 5


class UnusableAudioError(Exception):
    """Audio that will never transcribe, so trying again cannot help."""


class ReplayMissError(Exception):
    """No recorded transcript for this audio."""


class CannotTranscribeError(Exception):
    """This process cannot transcribe any memo, whatever the memo, so it must not claim
    one."""


VOICE_IMAGE = (
    "or run the transcriber from the voice image (`docker compose --profile voice up transcriber`)"
)
WHISPER_MISSING = (
    "faster-whisper is not installed, so the transcriber cannot run in local mode. "
    f"Install it with `uv sync --group voice`, {VOICE_IMAGE}."
)


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, fmt: str) -> str: ...


def audio_key(audio: bytes) -> str:
    return hashlib.sha256(audio).hexdigest()


def note_text(transcript: str) -> str:
    """The transcript as a note's text: one line of words, never empty or over NOTE_MAX."""
    text = " ".join(transcript.split())
    if not text:
        raise UnusableAudioError("no speech")
    if len(text) > NOTE_MAX:
        raise UnusableAudioError(f"the transcript is over {NOTE_MAX} characters")
    return text


class LocalWhisper:
    def __init__(self, size: str, models: str = "") -> None:
        if size not in WHISPER_REVISIONS:
            raise ValueError(f"the Whisper size must be one of {sorted(WHISPER_REVISIONS)}")
        self.size = size
        # Empty: Hugging Face's own cache folder.
        self.models = models
        self.model: WhisperModel | None = None

    def load(self) -> WhisperModel:
        if self.model is None:
            from faster_whisper import WhisperModel

            self.model = WhisperModel(
                f"Systran/faster-whisper-{self.size}",
                device="cpu",
                compute_type=COMPUTE_TYPE,
                cpu_threads=CPU_THREADS,
                download_root=self.models or None,
                revision=WHISPER_REVISIONS[self.size],
            )
        return self.model

    def transcribe(self, audio: bytes, fmt: str) -> str:
        """Decoded again here, as the api did, so a file that lied to the api still cannot
        run past MAX_SECONDS."""
        import numpy as np

        try:
            samples = decode(audio, fmt)
        except UndecodableError as error:
            raise UnusableAudioError("the audio does not decode") from error
        if seconds(samples) > MAX_SECONDS:
            raise UnusableAudioError(f"the audio is over {MAX_SECONDS} seconds")
        waveform = np.frombuffer(samples, dtype="<i2").astype(np.float32) / 32768.0
        segments, _ = self.load().transcribe(
            waveform,
            language=LANGUAGE,
            beam_size=BEAM_SIZE,
            # Skips silence, where Whisper invents text.
            vad_filter=True,
            # Stops repeat loops.
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments)


class ReplayTranscripts:
    """Transcripts recorded from real runs, one JSON line each: {"sha256", "text"}."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def transcribe(self, audio: bytes, fmt: str) -> str:
        key = audio_key(audio)
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                row = json.loads(line)
                if row["sha256"] == key:
                    return row["text"]
        raise ReplayMissError(f"no recorded transcript for audio {key} in {self.path}")


def from_settings(settings: Settings) -> Transcriber:
    """The transcriber the settings ask for. In local mode it first proves it can transcribe,
    before any job is claimed: ffmpeg and ffprobe are on PATH, faster-whisper imports, and
    the model loads, downloading if it must. A transcriber missing any of them would fail
    every memo's attempts on a fault that is not the memo's, and the last attempt deletes
    its audio. CannotTranscribeError says which and how to fix it."""
    if settings.transcribe_mode == "replay":
        return ReplayTranscripts(Path(settings.voice_transcripts))
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise CannotTranscribeError(
            f"{' and '.join(missing)} not found on PATH, so the transcriber cannot decode "
            f"memos. Install ffmpeg (brew install ffmpeg, or apt-get install ffmpeg), "
            f"{VOICE_IMAGE}."
        )
    try:
        importlib.import_module("faster_whisper")
    except ImportError as error:
        raise CannotTranscribeError(WHISPER_MISSING) from error
    whisper = LocalWhisper(settings.whisper_size, settings.whisper_models)
    try:
        whisper.load()
    except Exception as error:
        folder = settings.whisper_models or "Hugging Face's cache"
        raise CannotTranscribeError(
            f"The Whisper model {settings.whisper_size} could not be loaded into {folder}: "
            f"{type(error).__name__}: {str(error)[:500]}. Check that Hugging Face can be "
            "reached and that the folder is writable with room for the model."
        ) from error
    return whisper
