# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**视频码率检查器** (Video Bitrate Checker) — a Chinese-language Tkinter desktop app that scans a directory of video files, calls `ffprobe` on each, and flags which files meet a user-configured bitrate threshold (default 30000 kbps). Users can then move all passing files to a destination folder.

## Running

```bash
pip install tkinterdnd2
python video_checker.py
```

`ffprobe` is required. The app prefers a bundled copy at `ffprobe/ffprobe.exe` (next to the script, used when frozen by PyInstaller) and falls back to `ffprobe` on `PATH` (via `shutil.which`).

## Building (Windows)

The build produces a self-contained `dist/视频码率检查器.exe` with `tkinterdnd2` and `ffprobe` (plus its DLLs) bundled in.

```cmd
build.bat
```

`build.bat` will: (1) download FFmpeg from BtbN/FFmpeg-Builds into `ffprobe/` if not present, (2) `pip install tkinterdnd2 pyinstaller`, (3) clean `build/` and `dist/`, (4) run `pyinstaller video_checker.spec`. To rebuild manually:

```bash
pyinstaller video_checker.spec
```

`video_checker.spec` packs the `ffprobe/` directory via `get_ffprobe_data()` and includes `tkinterdnd2`/`tkdnd` as hidden imports — both are required for drag-and-drop to work in the frozen binary.

## Architecture

Single-file app (`video_checker.py`, ~590 lines). Three layers plus one cross-cutting concern:

**Video analysis** (pure functions, testable in isolation):
- `scan_video_files(directory, recursive)` — enumerates files matching `VIDEO_EXTENSIONS`
- `run_ffprobe(file_path)` — invokes ffprobe with a 30s timeout, returns parsed JSON or `None`. Hides the console window via `STARTUPINFO`/`CREATE_NO_WINDOW` on Windows
- `parse_video_info(data, full_path, base_path, bitrate_std)` — extracts resolution, fps, bitrate (prefers stream-level over format-level), codecs, duration, size, and relative path into a `VideoInfo` dataclass. Pass/fail is computed here as `bitrate_kbps >= bitrate_std`

**Data model** — `VideoInfo` dataclass with all display fields plus `full_path` (used by the mover) and `is_passing` (computed).

**GUI** — `VideoCheckerApp` builds the Tkinter UI:
- Top row: directory entry + browse + recursive checkbox + scan button
- Second row: bitrate threshold input
- Center: `ttk.Treeview` with color-coded `pass`/`fail` tags (green `#228B22` / red `#DC143C`)
- Bottom: destination path entry + "移动达标文件" button
- Drag-and-drop: `TkinterDnD.Tk()` root registers `DND_Files` on the tree; `_on_file_drop` parses `{file1} {file2}` Windows format

**Threading** — scanning runs on a daemon worker thread that pushes `(status|result|done)` tuples onto `result_queue`; the main thread drains the queue every 100ms via `root.after`. There are two parallel worker methods (`_scan_worker` for directory scans, `_scan_files_worker` for drag-dropped files) since their base-path semantics differ.

**File mover** — `_move_passing_files` confirms via dialog, then `shutil.move`s each passing file to the destination, using `_get_unique_dest` to append `_1`, `_2`, ... on name collisions. Note this is a *move*, not a copy.

## Key design notes

- `get_ffprobe_path()` is wrapped in `@lru_cache(maxsize=1)` — ffprobe lookup happens once per process
- Relative paths are displayed as `./` for the scan root and `./A/B` for subfolders, with backslashes normalized to forward slashes for cross-platform consistency
- The relative path is then concatenated with the file title in the tree's first column (`title_display` in `_add_result`)
- Title metadata is read from `format.tags` with case-insensitive key matching, falling back to the filename without extension
- `scanning` flag + `scan_btn` state prevent re-entrant scans
