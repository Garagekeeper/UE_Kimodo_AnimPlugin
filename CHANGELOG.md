# Changelog

## 1.0.0 — 2026-11-24

First stable release for Unreal Engine 5.8 (Windows).

### Generation
- Text prompt → Kimodo motion → retargeted `AnimSequence` for any skeletal mesh, with optional prompt interpretation through local CLI agents (codex, claude, gemini).
- Low-memory text encoder (default): Llama 3 8B layers streamed from disk, about 1–2 GB RAM instead of about 16 GB. `Low memory (exact)` gives results identical to the original CPU encoder.
- Prompt embedding cache, so changing only the seed or duration skips the text encoder.
- Seed input (`Use selected` / `Clear`), text encoder device and **Root motion** switch under `Advanced`.
- Root motion: the root bone follows the pelvis on the ground and the clip has `Enable Root Motion` set; off keeps the root fixed for in-place loops.

### Panel
- Preview on the left, Target / Prompt / Takes / Save sections on the right; Generate, Cancel and progress always visible.
- Takes list (up to 20, starred takes kept): compare, revise from the selected take, save. Takes persist across editor sessions and `Restore last session` brings them all back.
- Take previews are cached as assets under `Saved/Kimodo/TakeAssets` (`/KimodoTakes/Preview/T<number>_<words>_s<seed>_<id>`), so selecting or restoring a take does not re-import or retarget.
- Preview plays root motion in place of the clip's travel and draws the root path on the floor.
- Asset thumbnails for target mesh, IK Rig and Retargeter; remembered targets and save folder; suggested save names.
- `Ctrl+Enter` to generate, `Enter` to revise, editor notification when a generation finishes.

### Install
- One-click `Install Kimodo` with a bundled Windows / Python 3.11 `kimodo` wheel (no CMake, Git or MSVC needed).

### Fixes
- Editor no longer crashes on exit while unmounting `/KimodoTakes`.
- Restoring no longer switches docked tabs; notifications no longer raise the main window.
