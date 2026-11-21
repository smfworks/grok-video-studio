# Changelog

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
