# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**视频码率检查器** (Video Bitrate Checker) — a Chinese-language Tkinter desktop app that scans a directory of video files (or files dropped onto the window), calls `ffprobe` on each, and flags which files meet a user-configured bitrate threshold (default 30000 kbps) **and** sample-rate threshold (default 48 kHz). Users can then move all passing — or all scanned — files to a destination folder.

## Running

```bash
pip install tkinterdnd2
python video_checker.py
```

`ffprobe` is required. The app prefers a bundled copy at `ffprobe/ffprobe.exe` (used when frozen by PyInstaller, located via `sys._MEIPASS`) and falls back to `ffprobe` on `PATH` (via `shutil.which`).

## Building (Windows only)

`build.bat` produces a self-contained `dist/视频码率检查器.exe` with `tkinterdnd2` and `ffprobe` (plus its DLLs) bundled in. Steps: (1) download FFmpeg from BtbN/FFmpeg-Builds into `ffprobe/` if not present, (2) `pip install tkinterdnd2 pyinstaller`, (3) clean `build/` and `dist/`, (4) run `pyinstaller video_checker.spec`. To rebuild manually:

```cmd
pip install tkinterdnd2 pyinstaller
pyinstaller video_checker.spec
```

There is no equivalent macOS/Linux build script — `build.bat` is Windows-only (relies on `cmd` builtins + `powershell`). For dev on those platforms, just `python video_checker.py`.

`video_checker.spec` packs the `ffprobe/` directory via `get_ffprobe_data()` and includes `tkinterdnd2`/`tkdnd` as hidden imports — both are required for drag-and-drop to work in the frozen binary.

## Testing

No permanent test suite. Feature work is accompanied by a one-off assertion script in `docs/superpowers/specs/<date>-<topic>-assertions.py` that exits 0 on success. Run from the project root, e.g.:

```bash
python docs/superpowers/specs/2026-09-29-dnd-parsing-assertions.py
```

The pure functions (`parse_video_info`, `_parse_drop_data`, `format_duration`, `format_file_size`, `scan_video_files`) are the testable seam — import them directly, no fixtures needed.

## Architecture

Single-file app (`video_checker.py`, ~1018 lines). Four layers plus two cross-cutting concerns:

**Video analysis** (pure functions, testable in isolation):
- `scan_video_files(directory, recursive)` — enumerates files matching `VIDEO_EXTENSIONS`
- `run_ffprobe(file_path)` — invokes ffprobe with a 30s timeout, returns parsed JSON or `None`. Hides the console window via `STARTUPINFO`/`CREATE_NO_WINDOW` on Windows
- `parse_video_info(data, full_path, base_path, bitrate_std, sample_rate_std)` — extracts resolution, fps, bitrate (prefers stream-level over format-level), codecs, channels, sample rate, duration, size, container format, and relative path into a `VideoInfo` dataclass. `is_passing` requires **both** `bitrate_kbps >= bitrate_std` AND `sample_rate_passing` (no audio stream counts as passing)

**Data model** — `VideoInfo` dataclass holds all display fields (`rel_path`, `title`, `resolution`, `frame_rate`, `bitrate_kbps`, `video_codec`, `audio_codec`, `audio_channels`, `audio_sample_rate`, `duration`, `file_size`, `container_format`), plus thresholds (`bitrate_std`, `sample_rate_std`), pass flags (`is_passing`, `sample_rate_passing`), and `full_path` (used by the mover).

**GUI widget** — `VideoGridView` is a **custom Canvas-based grid**, not a `ttk.Treeview`. It builds: header row with drag-resize grab handles, scrollable `tk.Canvas` containing one `ttk.Frame` per data row, each row a grid of `tk.Label` cells. Per-cell foreground color is set independently via `_cell_fg`:
- Passing rows → all cells use `FG_PASS` (`#228B22` green)
- Failing rows → only the specific failing cells turn `FG_FAIL` (`#DC143C` red): always `title` and `result`; plus `bitrate` if `bitrate_kbps < bitrate_std`, plus `audio_sample_rate` if sample-rate check failed. Other cells stay default.

`VideoGridView.bind_drop(callback)` registers `DND_Files` on the outer frame so the whole widget (header + body) accepts drops.

**App** — `VideoCheckerApp` builds the Tkinter window:
- Row 1: path entry + 浏览 + 子文件夹 checkbox + 开始检测 button (toggles to 正在停止… / 检测中 when scanning; click cancels via `cancel_event`)
- Row 2: 码率标准 + 采样率标准 inputs, with 清除记录 pinned to the right via `padx=(0, 272)` (visual alignment with Row 1's path entry right edge)
- Center: `VideoGridView` with 12 columns (title / resolution / frame_rate / bitrate / video_codec / container_format / audio_codec / audio_channels / audio_sample_rate / duration / file_size / result)
- Bottom: 目标路径 + 浏览 + 移动达标文件 + 移动全部文件
- `<Control-o>` opens the browse-folder dialog
- Drag-and-drop: `TkinterDnD.Tk()` root; `_on_file_drop` uses `_parse_drop_data` to handle both Windows `{file1} {file2}` braced format and macOS/Linux space-separated format, then runs `_scan_files_worker`

**Threading** — scanning runs on a daemon worker thread that pushes `(status | result | done)` tuples onto `result_queue`; the main thread drains the queue every 100ms via `root.after` until a `done` arrives. There are two worker methods because their `base_path` semantics differ:
- `_scan_worker(directory, recursive, bitrate_std, sample_rate_std)` — directory scan; `base_path = directory` (relative paths anchored to the picked folder)
- `_scan_files_worker(files, bitrate_std, sample_rate_std)` — drag-dropped files; `base_path = os.path.dirname(files[0])` so the first file shows `./<title>` in the title column

The `cancel_event: threading.Event` is checked at the top of each per-file loop; the status bar then reports "已停止检测（已完成 N/M）" with the counts appended.

**Status bar** — a single line, one `status_var`. Terminal states append the tally via `_set_status(text, with_counts=True)` → `检测完成：共 10 · 达标 8 · 不达标 2`; intermediate states (就绪 / 正在检测… (3/10) a.mp4) show only the progress text. The "共 N" in the tally counts **results produced**; the M in "已完成 N/M" is the **total files found** — different sources, not a contradiction.

**File mover** — both `_move_passing_files` and `_move_all_files` follow the same shape: confirm via `askyesno` dialog, `os.makedirs(dest_dir, exist_ok=True)`, then `shutil.move` each file, with `_get_unique_dest` appending `_1`, `_2`, ... on name collisions. `_move_passing_files` filters `video_results` by `is_passing`; `_move_all_files` moves everything regardless of pass/fail. Both are **moves**, not copies. Path inputs are normalized to backslashes (`_browse_path`, `_browse_dest`, `_on_file_drop`) so Windows users don't see mixed slashes.

**Output directories are created on demand** — scanning/browsing never creates a directory. `snapshot` (screenshots) appears when you click 开始截图 in the dialog; `checked` (moved files) appears when you click 移动达标文件 / 移动全部文件. Two fields carry the paths, both derived from the source folder and overwritten on every scan (`_sync_derived_paths`):
- 迁移地址 → `<source>\checked`
- 截图地址 → `<source>\snapshot` (only a default — the dialog's own 截图地址 field is the one that counts)

The 浏览 button picks a **parent** folder and appends the fixed subfolder (`F\snapshot`, `F\checked`); typing a path into the field uses it verbatim.

## Key design notes

- `get_ffprobe_path()` is wrapped in `@lru_cache(maxsize=1)` — ffprobe lookup happens once per process
- Relative paths are displayed as `./` for the scan root and `./A/B` for subfolders, with backslashes normalized to forward slashes for cross-platform consistency
- The relative path is concatenated with the file title in the first column (see `VideoGridView._format_cell`, key `title`)
- Title metadata is read from `format.tags` with case-insensitive key matching, falling back to the filename without extension
- `scanning` flag + `scan_btn` state prevent re-entrant scans; drag-drop is also blocked while scanning (`_on_file_drop` shows an info dialog and returns)
- Container format: prefer the `format_name` token matching the file extension (e.g. an `.mp4` file with `format_name='mov,mp4,...'` resolves to `'mp4'`); fall back to the first token, then the extension, then `'N/A'`
- `DEFAULT_BITRATE_KBPS = 30000`, `DEFAULT_SAMPLE_RATE_KHZ = 48` — both editable from the UI; values are parsed with `float()` and must be positive
- Window: `1280x700`, `minsize(1000, 500)`, title `"视频检测v3.3"`
