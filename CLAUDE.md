# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **视频码率检查器** (Video Bitrate Checker) - a Chinese-language desktop application that scans video files in a directory and checks if their bitrate meets a configurable threshold. Built with Python + Tkinter.

## Running the Application

```bash
python video_checker.py
```

**External dependencies:**
- `ffprobe` - must be installed and available in PATH (part of ffmpeg)
- `tkinterdnd2` - Python package for drag-and-drop file support

## Building the Executable

```bash
pyinstaller video_checker.spec
```

On Windows, you can also use the provided build script:

```bash
build.bat
```

The spec file is configured for a Windows GUI app (no console window). Output goes to `dist/视频码率检查器.exe`.

## Architecture

Single-file application (`video_checker.py`) with three main layers:

1. **Video analysis** - `scan_video_files()`, `run_ffprobe()`, `parse_video_info()` use ffprobe JSON output to extract video metadata (bitrate, resolution, codec, frame rate, duration, etc.)
2. **Data model** - `VideoInfo` dataclass holds parsed video metadata and pass/fail status
3. **GUI** - `VideoCheckerApp` class builds a Tkinter UI with:
   - Directory browser + recursive scan toggle
   - Bitrate threshold input (default 30000 kbps)
   - Treeview table showing all video stats with color-coded pass/fail results
   - File mover to copy passing files to a destination folder
   - Drag-and-drop file support via tkinterdnd2
   - Worker thread for non-blocking scans (`_scan_worker` + queue-based IPC)
