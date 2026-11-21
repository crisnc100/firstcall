"""What a voice memo upload must be, checked before anything is stored (F36).

In order: at most MAX_BYTES (413); magic bytes naming one of five formats, and a declared
type that fits them (415); ffprobe finding exactly one audio stream in an allowed codec
(415); and at most MAX_SECONDS once decoded (422). The length comes from decoding, never
from a header, so a file cannot claim to be short. ffprobe and ffmpeg always run with the
demuxer the magic bytes named and only one protocol allowed, so a crafted file cannot make
them open another file or a URL.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

MAX_BYTES = 5_000_000
MAX_SECONDS = 180
# Decoding stops a second past the limit: enough to tell an overlong memo, and no more.
DECODE_SECONDS = MAX_SECONDS + 1
SAMPLE_RATE = 16_000
# 16-bit mono samples.
BYTES_PER_SECOND = SAMPLE_RATE * 2
PROBE_TIMEOUT = 10
DECODE_TIMEOUT = 60
# At most this many uploads are read and checked at once in one api process, so memos never
# take the thread pool or memory the board's other requests need. A full set answers 503.
MAX_CHECKS = 2
CHECKS = threading.BoundedSemaphore(MAX_CHECKS)
CODECS = frozenset({"opus", "aac", "mp3", "vorbis"})


@dataclass(frozen=True)
class Format:
    name: str
    # ffmpeg's demuxer for it.
    demuxer: str
    # Content types a browser may send for it.
    types: frozenset[str]


FORMATS = {
    f.name: f
    for f in (
        Format("webm", "matroska", frozenset({"audio/webm", "video/webm"})),
        Format("mp4", "mov", frozenset({"audio/mp4", "audio/x-m4a", "audio/m4a", "video/mp4"})),
        Format("wav", "wav", frozenset({"audio/wav", "audio/x-wav", "audio/wave"})),
        Format("mp3", "mp3", frozenset({"audio/mpeg", "audio/mp3"})),
        Format("ogg", "ogg", frozenset({"audio/ogg", "audio/opus", "application/ogg"})),
    )
}


class RefusedError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class UndecodableError(Exception):
    pass


@dataclass(frozen=True)
class Memo:
    data: bytes
    format: str
    seconds: float


def sniff(data: bytes) -> str | None:
    """The format the file's first bytes name, or None."""
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[4:8] == b"ftyp":
        return "mp4"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    return None


@contextmanager
def _saved(data: bytes) -> Iterator[str]:
    """The upload in a temporary file, deleted on exit, as ffprobe and ffmpeg's input. It
    must be seekable: an MP4 whose index follows its audio, as Safari and ffmpeg write
    them, cannot be read from a pipe once it outgrows ffmpeg's 32 KB read buffer."""
    with tempfile.NamedTemporaryFile(suffix=".audio") as handle:
        handle.write(data)
        handle.flush()
        yield f"file:{handle.name}"


def _streams(path: str, demuxer: str) -> list[dict[str, str]]:
    found = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-f", demuxer,
         "-show_entries", "stream=codec_type,codec_name", "-of", "json", path],
        capture_output=True, timeout=PROBE_TIMEOUT, check=False,
    )  # fmt: skip
    if found.returncode != 0:
        raise UndecodableError(found.stderr.decode(errors="replace")[-500:])
    return json.loads(found.stdout).get("streams", [])


def _decode(path: str, demuxer: str) -> bytes:
    try:
        found = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
             "-f", demuxer, "-i", path, "-map", "0:a:0", "-t", str(DECODE_SECONDS),
             "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"],
            capture_output=True, timeout=DECODE_TIMEOUT, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired as error:
        raise UndecodableError("decoding took too long") from error
    if found.returncode != 0 or not found.stdout:
        raise UndecodableError(found.stderr.decode(errors="replace")[-500:] or "no audio")
    return found.stdout


def decode(data: bytes, fmt: str) -> bytes:
    """16 kHz mono 16-bit samples of the first audio stream, at most DECODE_SECONDS."""
    with _saved(data) as path:
        return _decode(path, FORMATS[fmt].demuxer)


def seconds(samples: bytes) -> float:
    return len(samples) / BYTES_PER_SECOND


def check(data: bytes, content_type: str) -> Memo:
    """The upload as a memo, or RefusedError with the status to answer."""
    if len(data) > MAX_BYTES:
        raise RefusedError(413, f"a memo is at most {MAX_BYTES} bytes")
    fmt = sniff(data)
    if fmt is None:
        raise RefusedError(415, "a memo is WebM, MP4, WAV, MP3 or Ogg audio")
    declared = content_type.split(";")[0].strip().lower()
    if declared not in FORMATS[fmt].types:
        raise RefusedError(415, f"the file is {fmt}, but it was sent as {declared or 'nothing'}")
    demuxer = FORMATS[fmt].demuxer
    with _saved(data) as path:
        try:
            streams = _streams(path, demuxer)
        except (UndecodableError, subprocess.TimeoutExpired, ValueError) as error:
            raise RefusedError(415, "the audio could not be read") from error
        if len(streams) != 1 or streams[0].get("codec_type") != "audio":
            raise RefusedError(415, "a memo holds exactly one audio stream and no video")
        codec = streams[0].get("codec_name", "")
        if not (codec in CODECS or codec.startswith("pcm_")):
            raise RefusedError(415, "the audio is not Opus, AAC, PCM, MP3 or Vorbis")
        try:
            length = seconds(_decode(path, demuxer))
        except UndecodableError as error:
            raise RefusedError(415, "the audio could not be decoded") from error
    if length > MAX_SECONDS:
        raise RefusedError(422, f"a memo is at most {MAX_SECONDS // 60} minutes")
    return Memo(data, fmt, round(length, 2))
