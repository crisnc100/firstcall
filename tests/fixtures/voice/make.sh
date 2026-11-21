#!/usr/bin/env bash
# Writes the audio fixtures for the voice upload tests. They are committed, so the tests need
# ffmpeg's decoders but none of its encoders. A tone stands in for speech: these test the
# limits, not the transcriber.
set -euo pipefail
cd "$(dirname "$0")"

ff() { ffmpeg -nostdin -hide_banner -loglevel error -y "$@"; }
tone() { echo "sine=frequency=330:duration=$1:sample_rate=16000"; }

ff -f lavfi -i "$(tone 2)" -ac 1 -c:a libopus -b:a 12k ok.webm
ff -f lavfi -i "$(tone 2)" -ac 1 -c:a aac -b:a 16k ok.m4a
ff -f lavfi -i "$(tone 1)" -ac 1 -ar 8000 -c:a pcm_s16le ok.wav
ff -f lavfi -i "$(tone 2)" -ac 1 -c:a libmp3lame -b:a 16k ok.mp3
ff -f lavfi -i "$(tone 2)" -ac 1 -c:a libopus -b:a 12k ok.ogg
ff -f lavfi -i "$(tone 1)" -ac 1 -ar 8000 -c:a adpcm_ms adpcm.wav
# An MP4 with its index (moov) after the audio, as Safari and ffmpeg write them, and well
# past ffmpeg's 32 KB read buffer, so it decodes only from a seekable input.
ff -f lavfi -i "$(tone 30)" -ac 1 -c:a aac -b:a 16k moov-last.m4a
ff -f lavfi -i "$(tone 1)" -f lavfi -i "color=size=16x16:rate=2:duration=1" -ac 1 \
  -c:a libopus -b:a 12k -c:v libvpx -b:v 10k video.webm
# 3:05 of near silence, in long Opus frames to keep the file small.
ff -f lavfi -i "anullsrc=sample_rate=16000:channel_layout=mono" -t 185 \
  -c:a libopus -b:a 6k -frame_duration 60 long.webm
# The same audio with its header's duration rewritten to 10 seconds.
python3 - <<'PY'
import struct
data = bytearray(open("long.webm", "rb").read())
at = data.find(b"\x44\x89\x88")
assert at > 0, "no 8-byte Duration element"
data[at + 3 : at + 11] = struct.pack(">d", 10_000.0)
open("lying.webm", "wb").write(bytes(data))
PY
