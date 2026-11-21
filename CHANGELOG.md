# Changelog

## 1.3.0 (2026-11-21)
Required by the Grok Bot Video Studio template build that adds the **Strobe ad** video type (skill `vstudio-strobe-ad`). All three tools are code only: 0 AI generations, no network, no downloads.

### Added
- **Strobe cutter** (`scripts/vstudio_strobe_cut.py`): `plan` turns a music info file and an asset manifest (hero / object / message / texture stills, short clips, code-drawn frames) into a beat-locked EDL; `check` re-verifies its rules; `render` draws every frame (fixed wordmark, grain + halftone, AI label on the end hold) and encodes BT.709 H.264 with the music, plus an optional size-capped copy (`--max-mb 95` for X) and a frame-0 thumbnail. One cut per grid tick (1/8, 1/16 or 1/32 bar, picked so a tick is about 2-3 frames), 2-unit cells on every downbeat, N waves that open fast, slow down and end on holds, key-line holds of at least max(1.3 s, 0.1 s per word) snapped to beats, clips placed on bar lines, a per-asset use cap (default 4) with free variants (mirror flip, punch-in crop, accent duotone; never a mirrored text frame) before the cap is ever broken, wave cut-count balancing (default 15%), a fast final bar into a resolved ending or a loop-safe ending, and 9:16 / 16:9 / 1:1 output from the same EDL.
- **Brand frames** (`scripts/vstudio_brand_frames.py`): a brand system driven only by a brand name and one accent hex colour. Centred wordmark with an accent mark (square, dot, asterisk or none) that flips black or white from the luminance behind it, a soft halo on busy backgrounds and a text-coloured mark on accent backgrounds; grain + halftone pass; accent duotone; message-frame layouts (swiss poster, terminal, pure type, statement, list, index) that keep every text block out of the wordmark band and the bottom platform-UI zone in all three aspects; halftone, flash and ring textures; the small "AI GENERATED" end label; eye-line re-framing (`clear_y`: moves the crop window, then shifts the picture) that keeps a face's eyes off the wordmark on frame 0 and the thumbnail in every aspect; Gate-1 composites and labelled contact sheets. Fonts: Anton / Archivo Black / IBM Plex Mono when installed, otherwise Inter (installed by install.sh) and DejaVu.
- **Audio finish** (`scripts/vstudio_audio_finish.py`): `analyze` fits a straight beat grid (tempo folded into 90-180 BPM, refined on onsets) and finds the downbeats from low-end energy (the bass hits) and the last strong downbeat; `finish` builds a bar-exact body (optionally a section looped on the bar line), ends on a final downbeat hit with its natural decay and a synthetic hall tail (or a bar-line fade, or a loop-safe cut) instead of stopping mid-note, adds synthesized ratchet risers into each wave and the final hit, masks loop seams with a half-bar ratchet and sub boom, and normalizes to -14 LUFS with true peak <= -1 dBTP (two-pass linear loudnorm, limiter fallback). Writes the music info JSON the cutter reads. ffmpeg + numpy (librosa only for `analyze`).
- `tests/test_strobe.py` (34 tests): brand, layout band-clearance in every aspect, eye-line shift, audio grid/ending/risers/loudness, cutter rules (downbeats on cuts, key holds, use cap and variants, wave balance, loop ending, rule-check failures) and a small end-to-end render. All fixtures (images, a test clip, a synthetic drum-and-bass track) are generated in code.

### Changed
- Nothing in the existing scripts, kits, installer, requirements or CI changed; install.sh already copies every `scripts/*.py`.

## 1.2.0 (2026-11-21)

### Added
- **Film grade** (`scripts/vstudio_film_grade.py`): light, medium and strong film look (muted saturation, warmth, soft highlights, grain, halation, vignette and gate weave) with optional 2.39:1 letterbox and per-shot `--yoff` for head-room. Prints an ffmpeg filter string, or `apply` / `compare` / `headroom` modes. Letterbox defaults off on vertical or square frames; `--letterbox` forces it on. Medium at 1920x1080, 24 fps, is filter-string identical to the original grade.
- `tests/test_film_grade.py`: filter-string building tests (no ffmpeg required).

The music-video v2 lessons (mixed short and long shots, detail inserts, the Medium film grade, letterbox head-room checks, and still-fault catch list) live in the vstudio-music-video skill; this tool is the reusable grade those lessons call. This repo has no separate docs folder.


## 1.1.0 (2026-11-17)
Required by Grok Bot Video Studio template v10. Everything new is code-drawn: 0 AI image or video generations.

### Added
- **Numbers on Screen explainer** (`kits/numbers_extract.py`, `kits/numbers_kit.py`): spreadsheet (CSV/XLSX) intake into `cells.json`, a draft `numbers.json`, and an owner confirmation table (`confirm.md`) that flags mismatches and low-confidence cells (e.g. hand-read photo cells) until the owner checks them. Figures are recomputed with exact Decimal round-half-up math. Breakdown, annualize and before/after compare scenes, with an optional count-up. An EXAMPLE FIGURES chip marks made-up data. Stills and renders refuse to run until every figure traces to a confirmed cell.
- **Fix It Case Study** (`kits/case_study_kit.py`): a problem → cause → fix → result story with a 1.5 s moving hook, an acting Chalk Chef (pose cues with squash-and-stretch; no mouth motion over voiceover), kinetic captions, a big before/after result with count-up, an owner-filled CTA, an upbeat original bed with SFX at -14 LUFS, and separate 9:16, 16:9 and 1:1 layouts with a text floor ≥ 5% of frame height. Per-cue voice uses local Piper or opt-in xAI TTS (`XAI_TTS_API_KEY`; cached by hash, cost logged to the ledger), with local faster-whisper word alignment and `.srt` output. `check()` blocks client names without written permission, real result figures without an owner source, and unlabeled example figures.
- **Visual Kit** (`kits/visual_kit.py`, `kits/vkit.py`): versioned, immutable kit folders (kit.json with a sha256 asset manifest, character refs, shots, graphics, CHARACTER BLOCK and STYLE LOCK text), and three printable vector PDFs (checklist, opening/closing, par levels) as free downloads. `vkit.py` is the shared core: chalkboard and chalk grain, the posable chef, kitchen diagrams, captions and chips, SFX and music bed, the parallel BT.709 renderer (real primaries/transfer tags) with two-pass loudnorm, and a dependency-free PDF writer.
- `kits/promo_kit.py`: the code-drawn promo kit, now shipped in the repo (it was embedded in the template skill before). Its `music()` is shared by the other kits.
- `tests/test_kits.py` (18 tests) and `examples/` fixtures (made-up, labeled).
- `VERSION` file and this changelog.

### Changed
- `install.sh` also installs `kits/` into `$VSTUDIO_HOME/kits/`, copies `VERSION`, and fetches the kit fonts (Caveat Brush, Kalam Bold, Inter; SIL OFL 1.1) from google/fonts at a pinned commit with sha256 checks.
- `requirements.txt` adds Pillow, openpyxl and requests.
- CI sets `VSTUDIO_HOME` so the kit tests use the pinned fonts, and runs the installer with `bash` so it doesn't depend on the file mode.
- Kits read `VSTUDIO_HOME` (default `/workspace/vstudio`) instead of hard-coded paths.

## 1.0.0
First release for Grok Bot Video Studio template v9: budget/caps/ledger, the xAI Imagine and TTS adapters, Piper voice, transcription, captions, assembly, technical QA, beats, character packs and media helpers.
