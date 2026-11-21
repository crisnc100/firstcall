# Voice gold: sources and licences

The clips in `seed-11/` are synthetic speech: Piper read each seed-11 note script aloud,
once clean and once degraded (`firstcall corpus voice`). No real person was recorded for
them.

## The tool

Piper (`piper-tts` 1.8.0) is GPL-3.0-or-later. It ran as a separate program, outside this
project's dependencies, and no Piper code is in this repository; only the audio it made is.
The voice models came from `rhasspy/piper-voices` on Hugging Face (repository licence MIT)
at revision `e9c8a39444359388adaa94255becb37a5a2b8e51`.

## The voices

A voice is used only when its model card allows reuse of what it says: CC0, CC BY,
CC BY-SA, MIT or public domain, both for the voice's own recordings and for any model it
was finetuned from. Each voice reads 15 scripts, in both of their clips.

| Voice | Speaks | Recordings | Licence | Finetuned from | Its licence |
|---|---|---|---|---|---|
| `en_US-joe-medium` | US English | [OHF-Voice voice datasets](https://github.com/OHF-Voice/voice-datasets) | CC0 | rdh (one Flemish speaker) | CC0 |
| `en_US-kathleen-medium` | US English | [OHF-Voice voice datasets](https://github.com/OHF-Voice/voice-datasets) | CC0 | hifi_tts_92 | CC BY 4.0 |
| `en_US-kristin-medium` | US English | [LibriVox](https://librivox.org) readings | public domain | none | none |
| `en_US-mike-medium` | US English | [OHF-Voice voice datasets](https://github.com/OHF-Voice/voice-datasets) | CC0 | rdh (one Flemish speaker) | CC0 |
| `en_US-ljspeech-high` | US English | [LJ Speech](https://keithito.com/LJ-Speech-Dataset/) | public domain | none | none |
| `en_GB-cori-high` | UK English | [LibriVox](https://librivox.org) readings by Cori Samuel | public domain | none | none |
| `en_GB-alba-medium` | UK English | [Alba speech corpus](https://datashare.ed.ac.uk/handle/10283/3270), University of Edinburgh | CC BY 4.0 | hifi_tts_92 | CC BY 4.0 |
| `en_GB-vctk-medium:p245` | Irish English (Dublin) | [CSTR VCTK Corpus 0.92](https://datashare.ed.ac.uk/handle/10283/3443), speaker p245 | CC BY 4.0 | hifi_tts_92 | CC BY 4.0 |
| `en_GB-vctk-medium:p326` | Australian English (Sydney) | [CSTR VCTK Corpus 0.92](https://datashare.ed.ac.uk/handle/10283/3443), speaker p326 | CC BY 4.0 | hifi_tts_92 | CC BY 4.0 |
| `en_GB-vctk-medium:p237` | Scottish English (Fife) | [CSTR VCTK Corpus 0.92](https://datashare.ed.ac.uk/handle/10283/3443), speaker p237 | CC BY 4.0 | hifi_tts_92 | CC BY 4.0 |

The VCTK accents are those VCTK's speaker information gives. hifi_tts_92 is Piper's base
model trained on reader 92 (Cori Samuel) of the Hi-Fi Multi-Speaker English TTS Dataset.

## Attribution

The CC BY 4.0 sources behind these clips:
- **CSTR VCTK Corpus** (version 0.92): Junichi Yamagishi, Christophe Veaux and Kirsten
  MacDonald, University of Edinburgh, Centre for Speech Technology Research, 2019.
  <https://doi.org/10.7488/ds/2645>
- **Alba speech corpus**: University of Edinburgh DataShare.
  <https://datashare.ed.ac.uk/handle/10283/3270>
- **Hi-Fi Multi-Speaker English TTS Dataset**: Evelina Bakhturina, Vitaly Lavrukhin, Boris
  Ginsburg and Yang Zhang, NVIDIA, 2021. <https://www.openslr.org/109/>
