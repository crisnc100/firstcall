"""The part of faster-whisper 1.2.1 that firstcall/voice/transcribe.py calls, for pyright.

faster-whisper is installed only in the transcriber's image (the voice group in
pyproject.toml), so pyright reads this stub everywhere. It declares only what that file
uses, with the names and types of faster-whisper 1.2.1's WhisperModel and Segment. A real
transcription in the voice image is the check that the two still agree.
"""

from collections.abc import Iterable
from typing import Any

from numpy.typing import NDArray

# faster_whisper.transcribe.Segment, which the package does not export at its top level.
class _Segment:
    text: str

class WhisperModel:
    def __init__(
        self,
        model_size_or_path: str,
        *,
        device: str = ...,
        compute_type: str = ...,
        cpu_threads: int = ...,
        download_root: str | None = ...,
        revision: str | None = ...,
    ) -> None: ...
    def transcribe(
        self,
        audio: NDArray[Any],
        *,
        language: str | None = ...,
        beam_size: int = ...,
        vad_filter: bool = ...,
        condition_on_previous_text: bool = ...,
    ) -> tuple[Iterable[_Segment], object]: ...
