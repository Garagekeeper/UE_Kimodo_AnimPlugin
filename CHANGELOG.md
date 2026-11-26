# Changelog

## 1.2.0 — 2026-11-26

### Added
- **Body keys** (under Takes): set where a hand, a foot or the hips (the five parts Kimodo can pin) should be at given times, relative to the selected take's pose (cm right / up / forward), then `Generate with keys` regenerates with Kimodo end-effector constraints using the take's prompt, seed, length and steps. A hips key also moves the body's ground position and height, so lowering it crouches. Includes `+ Key at playhead` and a `Big wave preset` (big, slow hello wave: 25 cm → 67 cm swing in the test case).
- **Sequencer-style key editing**: a key timeline under the preview (tracks RH/LH/RF/LF/Hip; click a diamond to select, drag it in time, double-click or right-click to add, Delete/K), a `+ Add Key` bar with a part selector, and the engine's move gizmo in the preview (red forward / green right / blue up, character-relative, with plane handles) that updates the selected key's cm values live; other keys show as cyan markers. The area under the preview is resizable and always shows all five tracks. The Body keys list shows every key, sorted by part and time; a part can't have two keys at the same time.
- **Keys per take**: switching takes loads that take's keys, edits are saved with the take (and in the restored session), and a take made with keys starts with no keys (its pose already includes them, so reusing them would apply the offsets twice). Takes also remember their requested length.
- `Advanced > Steps`: diffusion step count (1-1000, default 100, `Default` button). Takes remember their step count, and takes not made with 100 steps show it in the status line. Same seed + steps + duration + model reproduces a take; the worker gives byte-identical output to the Kimodo CLI's `--diffusion_steps`.

### Changed
- Preview camera works like an editor viewport: left-drag orbits, the wheel zooms, holding the right mouse button with WASD / QE flies (the orbit center then moves in front of the camera).

## 1.1.1 — 2026-11-25

### Added
- Prebuilt download `KimodoAnimation-<version>-Win64-UE5.8.zip` with the compiled editor DLL, so Blueprint-only projects can use the plugin without Visual Studio (first published for 1.1.0, verified on a separate PC). The source zip stays for C++ projects and other engine hotfixes.
- Automated tests in `Tests/` (unit, Kimodo worker integration, headless editor) with `Tests/run_tests.ps1`.

### Fixes
- Starred takes no longer count toward the 20-take limit: up to 20 unstarred takes are kept, plus every starred take.
- The progress line says what it is waiting for: `Starting Kimodo` / `Loading Kimodo model` (first take only), `Loading text encoder` and `Encoding new prompt (about 30-40 s, cached after)` (new prompts only), `Preparing generation`, `Generating motion N%`. It no longer shows `Loading Kimodo` at the start of every take once Kimodo is resident, and `Finishing the cancelled take` appears only after a Cancel.

## 1.1.0 — 2026-11-25

### Added
- **Generate ×N**: a `×1 / ×3 / ×5 / ×10` selector right next to the Generate button makes several takes from one prompt in a row. The prompt is interpreted once; each take gets its own seed (random, or `seed, seed+1, …` when a seed is entered). Progress shows `Take 2/5`; Cancel keeps the takes already made. Revise always makes one take.
- **Resident Kimodo worker**: the model is loaded once into a background process (`Scripts/kimodo_worker.py`) and reused, so a take with a cached prompt drops from about 17 s to about 4 s (RTX 5060, 2 s clip). The worker is unloaded after 10 minutes without generating, when the panel tab is closed, or when the editor exits. Cancel keeps it loaded: the cancelled request finishes in the background and its result is dropped. Output is byte-identical to the previous per-run process for the same seed.

### Changed
- The Prompt input (with Duration and Advanced) moved from the top of the panel to the always-visible bar at the bottom, right above Generate, so typing, choosing ×N and generating happen in one place. Advanced remembers whether it was expanded.

### Fixes
- Generating before `Restore last session` no longer gives new takes the same numbers as the previous session's takes. Restoring also renumbers duplicates already saved by 1.0.0.

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
