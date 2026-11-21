# Grok Video Studio

Production scripts for the **Grok Bot Video Studio** template: an end-to-end video bot (explainers, avatar shorts, sequenced stories with character locks, music videos, code-drawn promos, beat-cut strobe ads, Numbers on Screen explainers, Fix It case studies and reusable visual kits) that runs on Grok Imagine and, opt-in, the xAI API.

The Grok Bot template carries the profile, skills and routines. This repo carries the code those skills call. The template's setup skill fetches **one pinned tag/commit**, checks its hash, runs the tests, and installs into `/workspace/vstudio/`.

**Version: 1.3.0** (see [VERSION](VERSION) and [CHANGELOG.md](CHANGELOG.md)). The template build with the Strobe ad video type requires grok-video-studio v1.3.0; the previous build uses v1.2.0, template v10 requires v1.1.0 or later, and template v9 uses v1.0.0.

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
| `vstudio_film_grade.py` | Film look (light/medium/strong): print the ffmpeg filter, or apply it to a file (audio copied untouched), plus a side-by-side compare still and a 2.39:1 letterbox head-room check. Letterbox defaults off on vertical frames |
| `vstudio_strobe_cut.py` | Beat-synced strobe cutter: `plan` an EDL from a music info file and an asset manifest (cuts on every tick, holds on bass hits, waves, key-line holds, use cap with free variants, wave balancing, resolved or loop ending), `check` its rules, `render` with the wordmark, grain and AI end label (9:16, 16:9, 1:1; optional size cap for X) |
| `vstudio_brand_frames.py` | Brand system from a name + one accent hex: auto black/white wordmark with halo, grain + halftone, message-frame layouts (swiss, terminal, type, statement, list, index) and textures, eye-line-safe composites, AI label, contact sheets |
| `vstudio_audio_finish.py` | Short music beds: beat grid and downbeats, bar-exact body or bar-line loop, resolved final-hit ending (or fade / loop), synthesized ratchet risers into each wave, loop-seam masking, -14 LUFS / TP <= -1 dBTP |

### Code-drawn kits (`kits/`, installed to `$VSTUDIO_HOME/kits/`, 0 AI generations)
| Kit | Purpose |
|---|---|
| `vkit.py` | Shared drawing and render core: chalkboard look, posable Chalk Chef (`chef_sprite`, poses and moods, squash-and-stretch, mouth never moves with the voice), kitchen diagrams, captions and chips, original SFX and an upbeat music bed, a parallel BT.709 render with two-pass loudnorm, and a small vector-PDF writer for printables |
| `numbers_extract.py` | Numbers on Screen intake: spreadsheet (CSV/XLSX) to `cells.json`, a draft `numbers.json`, and `confirm.md` that flags mismatches and low-confidence cells for the owner |
| `numbers_kit.py` | Numbers on Screen renderer: exact Decimal arithmetic recomputed from confirmed cells, breakdown/annualize/compare scenes, and an EXAMPLE FIGURES chip for made-up data. It refuses to draw unconfirmed figures |
| `case_study_kit.py` | Fix It Case Study renderer (problem → cause → fix → result): acting presenter, kinetic captions, before/after count-ups, owner-filled CTA, 9:16 / 16:9 / 1:1 layouts with a text floor ≥ 5% of frame height, per-cue voice (local Piper, or opt-in xAI TTS via `XAI_TTS_API_KEY`, cached and ledger-logged), local word alignment and `.srt`. It blocks client names without permission and real results without an owner source |
| `visual_kit.py` | Builds a versioned, immutable Visual Kit folder (kit.json with sha256 manifest, character refs, shots, graphics, three printable PDFs). Kitchen example included |
| `promo_kit.py` | Code-drawn promo kit and the shared `music(path, dur, bpm, seed)` |

`examples/` holds the made-up fixtures the tests use (a plate-cost sheet and a Fix It case.json, both labeled as examples).

## Strobe ad (v1.3.0)
A 15-25 s brand ad cut to the beat: stills, short clips and code-drawn message frames under a fixed centred wordmark, in one accent colour. The template skill `vstudio-strobe-ad` covers the brief and approval gates; the code path is:
```bash
B=/workspace/vstudio/bin
python3 $B/vstudio_brand_frames.py frames frames_spec.json frames --preview          # message frames + frames.json
python3 $B/vstudio_audio_finish.py analyze song.wav analysis.json                      # check tempo / downbeat
python3 $B/vstudio_audio_finish.py finish song.wav music.wav --info music.json --bars 12 --waves 3 --ending hit
python3 $B/vstudio_strobe_cut.py plan manifest.json music.json edl.json                # exit 2 = a rule failed
python3 $B/vstudio_strobe_cut.py render manifest.json edl.json final.mp4 --audio music.wav --thumbnail thumb.jpg --max-mb 95
```
Nothing is brand-specific: the brand name and accent come from the spec and manifest (formats in each script's header docstring). Message copy must come from the owner's own text, and characters must be fictional (or the owner, with consent, on the xAI route only).

## Install and test
```bash
bash install.sh /workspace/vstudio         # scripts, kits, config, pinned Piper + voice and kit fonts (sha256-checked), Python deps
VSTUDIO_HOME=/workspace/vstudio VSTUDIO_PIPER_DIR=/workspace/vstudio/piper python3 -m unittest discover -s tests -v
```
Needs Linux x86_64, Python 3.11+, ffmpeg with libass/freetype, and fontconfig with DejaVu Sans. Kits find fonts in `$VSTUDIO_FONTS`, `/usr/share/fonts`, then `$VSTUDIO_HOME/fonts` (where install.sh puts them). `VSTUDIO_HOME` defaults to `/workspace/vstudio`.

## Status
- The xAI video and TTS adapters are tested against the documented request/response shapes (mocked HTTP plus a real local HTTP server). **Live API verification is pending** until an owner runs a first call with their own key. The case-study kit's opt-in xAI TTS route was verified live while making the template v10 sample (11 cues).
- The Piper voice test runs real TTS end to end and checks the transcript match (≥ 0.95).
- The kit tests render frames for every layout, check figure provenance, client-permission and example-label gates, and build a Visual Kit (no network, no keys).
- The strobe tests generate their own images, test clip and drum-and-bass track, check the cut rules and audio ending, and render a small end-to-end video (no network, no keys).

## License
MIT. See [LICENSE](LICENSE) and [CREDITS](CREDITS).
