# Grok Video Studio

Production scripts for the **Grok Bot Video Studio** template: an end-to-end video bot (explainers, avatar shorts, sequenced stories with character locks, music videos) that runs on Grok Imagine and, opt-in, the xAI API.

The Grok Bot template carries the profile, skills and routines. This repo carries the code those skills call. The template's setup skill fetches **one pinned tag/commit**, checks its hash, runs the tests, and installs into `/workspace/vstudio/`.

## What's here
| Script | Purpose |
|---|---|
| `vstudio_budget.py` | Cost estimates, owner-set caps (no defaults; explicit dated "no cap" choices), ledger, demo allowance |
| `vstudio_providers.py` | xAI Imagine API adapter (`generate`: submit/poll/download) and xAI TTS (`tts`: `POST /v1/tts`), with cap checks, logging and capped retries. Key from `XAI_API_KEY` only |
| `vstudio_voice.py` | Local narration with Piper TTS (free, no key) |
| `vstudio_transcribe.py` | Local speech-to-text (faster-whisper), script-match scoring |
| `vstudio_captions.py` | SRT from word timings, caption burn-in, "AI-generated" tag |
| `vstudio_assemble.py` | Clip assembly, crossfades, voice/music mix with ducking, loudness |
| `vstudio_qa_tech.py` | Technical QA (black/freeze/silence, loudness, duration, size) |
| `vstudio_beats.py` | Beat detection and beat-aligned shot slots |
| `vstudio_pack.py` | Character-pack validation, import screening, owner-likeness consent |
| `vstudio_media.py` | ffprobe helpers |

## Install and test
```bash
./install.sh /workspace/vstudio          # scripts, config, pinned Piper + voice (sha256-checked), Python deps
VSTUDIO_PIPER_DIR=/workspace/vstudio/piper python3 -m unittest discover -s tests -v
```
Needs Linux x86_64, Python 3.11+, ffmpeg with libass/freetype, and fontconfig with DejaVu Sans.

## Status
- The xAI video and TTS adapters are tested against the documented request/response shapes (mocked HTTP plus a real local HTTP server). **Live API verification is pending** until an owner runs a first call with their own key.
- The Piper voice test runs real TTS end to end and checks the transcript match (≥ 0.95).

## License
MIT. See [LICENSE](LICENSE) and [CREDITS](CREDITS).
