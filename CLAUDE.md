# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**视频码率检查器** (Video Bitrate Checker) — a Chinese-language PySide6 desktop app that scans a directory of video files (or files dropped onto the window), calls `ffprobe` on each, and flags which files meet a user-configured bitrate threshold (default 30000 kbps) **and** sample-rate threshold (default 48 kHz). Users can then move all passing — or all scanned — files to a destination folder. A secondary "定点截图" (fixed-timestamp screenshot) dialog extracts frames at chosen timestamps for eyeball comparison.

The GUI was Tkinter (v3.3) until v4.0 replaced it with PySide6 / `QTableView`. That rewrite lived on the `refactor/qtableview` branch and was fast-forward-merged into `main` (branch deleted) — **`main` is the PySide6 line, currently v4.2** (v4.0 = the Qt rewrite, v4.1 = 定点截图的「查看已有截图」, v4.2 = 「下载海报」). The behavioral spec lives in `docs/产品文档.md` and is framework-agnostic — it is the acceptance baseline.

## Running

```bash
pip install PySide6
python video_checker.py
```

`ffprobe` is required. The app prefers a bundled copy at `ffprobe/ffprobe.exe` (used when frozen by PyInstaller, located via `sys._MEIPASS`) and falls back to `ffprobe` on `PATH` (via `shutil.which`).

On a headless machine, set `QT_QPA_PLATFORM=offscreen` (the assertion scripts do this for you).

## Building (Windows only)

`build.bat` produces a self-contained `dist/视频码率检查器.exe` with PySide6 and `ffprobe` (plus its DLLs) bundled in. Steps: (1) check Python is **3.10+** (a real version comparison, not just "is python there"), (2) download FFmpeg from BtbN/FFmpeg-Builds into `ffprobe/` if not present, (3) `pip install PySide6 pyinstaller`, (4) clean `build/` and `dist/`, (5) run `pyinstaller video_checker.spec`. To rebuild manually:

```cmd
pip install PySide6 pyinstaller
pyinstaller video_checker.spec
```

There is no equivalent macOS/Linux build script — `build.bat` is Windows-only (relies on `cmd` builtins + `powershell`). For dev on those platforms, just `python video_checker.py`.

`video_checker.spec` packs the `ffprobe/` directory via `get_ffprobe_data()` and declares a `PYSIDE6_EXCLUDES` list of PySide6 modules the app doesn't use. The excludes were verified against the *link-time* dependency graph (`otool -L`): `QtWidgets` links only `QtCore` + `QtGui` — **not** `QtNetwork`. What the excludes actually buy: `QtNetwork`'s extension module and its `tls` / `networkinformation` plugin families (OpenSSL backends) drop out of the bundle. The Qt C++ frameworks that survive (e.g. `QtSvg`, `QtQml`) come in via **plugins**, which Python-module excludes cannot remove.

## Testing

No permanent test suite. Feature work is accompanied by a one-off assertion script in `docs/superpowers/specs/<date>-<topic>-assertions.py` that exits 0 on success. Run from the project root, e.g.:

```bash
QT_QPA_PLATFORM=offscreen python3 docs/superpowers/specs/2026-10-27-qt-table-assertions.py
```

Run **every** script before claiming a change is done:

```bash
for f in docs/superpowers/specs/*-assertions.py; do QT_QPA_PLATFORM=offscreen python3 "$f" >/dev/null || echo "FAIL $(basename $f)"; done
```

The pure functions in `video_core.py` (`parse_video_info`, `parse_threshold`, `format_duration`, `format_file_size`, `format_title`, `truncate_text`, `scan_video_files`, `parse_snapshot_filename`, `scan_existing_snapshots`, `match_snapshots`, `time_token_sort_key`, `read_tabular_document`, `guess_clipname_column`, `guess_link_columns`, `guess_partname_column`, `unique_poster_path`, `render_filename`, `extension_for_url`, `poster_default_dir`, `build_poster_jobs`, `download_poster`, `_cell_fg`, `_extract_sort_key`, `_next_sort_state`, `_header_text_with_arrow`) are the testable seam — import them directly, no fixtures needed. `video_checker.py` re-exports them so older scripts that do `from video_checker import X` still work.

## Architecture

Three modules, ~4.1k lines total:

| File | Role |
|---|---|
| `video_core.py` (1140) | Pure logic. **No Qt, no Tk.** |
| `video_qt.py` (3087) | The whole Qt GUI: models, main window, snapshot dialog, poster dialog, zoom window. |
| `video_checker.py` (36) | Entry point + re-exports of `video_core` names. **CRLF line endings.** |

**Video analysis** (`video_core.py`, pure functions, testable in isolation):
- `scan_video_files(directory, recursive)` — enumerates files matching `VIDEO_EXTENSIONS` (36 extensions, case-insensitive; `os.walk` when recursive, `os.listdir` otherwise, unsorted)
- `run_ffprobe(file_path)` — invokes ffprobe with a 30s timeout, returns parsed JSON or `None`. Hides the console window via `STARTUPINFO`/`CREATE_NO_WINDOW`, **guarded by `sys.platform == 'win32'`** (before 2026-10-27 there was **no** guard — the worker thread died on macOS/Linux)
- `extract_frame(input_path, seconds, output_path)` — ffmpeg, writes a **640px-wide PNG**
- `parse_video_info(data, full_path, base_path, bitrate_std, sample_rate_std)` — extracts resolution, fps, bitrate (prefers stream-level over format-level), codecs, channels, sample rate, duration, size, container format, and relative path into a `VideoInfo` dataclass. `is_passing` requires **both** `bitrate_kbps >= bitrate_std` AND `sample_rate_passing` (no audio stream counts as passing)
- `parse_threshold(text)` — parses a threshold; rejects non-positive **and** `inf`/`nan`
- `truncate_text(text, col_width, measure)` — pixel-level truncation ending in three ASCII dots (not `…`)
- `parse_snapshot_filename(name)` → `(base, token)` | `None` — parses a screenshot filename `{title}_t{idx}_{HH-MM-SS-FF}.png` back into its parts. The regex is **greedy + backtracking**, so it takes the **rightmost** `_t<digits>_` — exactly symmetric to the writer in `video_qt._snapshot_worker`. The token comes back in `:` form (`'00:00:07:24'`), matching the timepoint input boxes
- `scan_existing_snapshots(output_dir, progress=None)` → `list[SnapshotFile]` — first level only, **never creates the directory** (directory creation has exactly one gate: `ensure_snapshot_dir`). The optional `progress(done, total)` callback fires per file — on a network drive the per-file `getmtime` round-trips are slow enough that the UI needs to know
- `match_snapshots(files, base)` → `{token: SnapshotFile}` — picks one file per (video, timepoint); same timepoint twice → the **newer mtime** wins
- `time_token_sort_key(token)` → `(h, m, s, frame)` — numeric sort, because string sort puts `00:10` before `00:02`
- `DEFAULT_BITRATE_KBPS = 30000` / `DEFAULT_SAMPLE_RATE_KHZ = 48` live **here** — core is the single home; the GUI layer imports them

**Data model** — `VideoInfo` dataclass holds all display fields (`rel_path`, `title`, `resolution`, `frame_rate`, `bitrate_kbps`, `video_codec`, `audio_codec`, `audio_channels`, `audio_sample_rate`, `duration`, `file_size`, `container_format`), plus thresholds (`bitrate_std`, `sample_rate_std`), pass flags (`is_passing`, `sample_rate_passing`), and `full_path` (used by the mover).

**Table** — `VideoTableModel(QAbstractTableModel)` + `QTableView`. The table area contains **no native child windows** — that is the root fix for "restore from taskbar shows a black table area" (a Qt top-level window is rendered offscreen and blitted once, so there is no half-painted intermediate state).

- Columns are declared once in `VIDEO_COLUMNS: list[ColumnSpec]` (12 columns: 序号 / title / resolution / frame_rate / bitrate / spec / audio_channels / audio_sample_rate / color / duration / file_size / result). `ColumnSpec.key is None` means the 序号 column.
- **Coloring** goes through `Qt.ForegroundRole` → the pure `video_core._cell_fg(info, key)`:
  - Passing rows → all cells use `FG_PASS` (`#228B22` green)
  - Failing rows → only the specific failing cells turn `FG_FAIL` (`#DC143C` red): always `title` and `result`; plus `bitrate` if `bitrate_kbps < bitrate_std`, plus `audio_sample_rate` if sample-rate check failed. Other cells return `None` so Qt uses the palette default (never hard-coded black).
  - The 序号 column returns `None` for `ForegroundRole` — **never colored**, not even on passing rows.
- **Truncation** happens in the model's `data()` via `QFontMetrics(ui_font()).horizontalAdvance`; `TruncatingDelegate` sets `textElideMode = Qt.ElideNone` so Qt does not elide a second time (Qt's default is a single `…`, the spec wants three ASCII dots).
- **Tooltip** via `Qt.ToolTipRole`: the full text is returned **only when it differs from the truncated text** — untruncated cells get no tooltip.
- **Alignment**: exactly two kinds — `title` left (`Qt.AlignLeft | Qt.AlignVCenter`), everything else centered. Applied through `Qt.TextAlignmentRole` in both `data()` and `headerData()` (`QHeaderView` reads the model's alignment role).
- **Sorting**: `on_header_clicked` → `_next_sort_state` (three-state cycle: none → asc → desc → none; switching column resets to ascending) → `_apply_sort`, which uses Python's stable `list.sort` with `_extract_sort_key`. Non-sortable columns do **nothing at all** (not even a cursor change). After sorting, `changePersistentIndex` keeps the view's current/selection pointing at the same file.
- **Column widths are owned by Python**: the model holds `_widths: dict[int, int]`. The view pushes user drags back via `header.sectionResized` → `model.set_column_width()`, which re-truncates and emits `headerDataChanged` + `dataChanged`. Minimum width is 20px; the 序号 column is `QHeaderView.Fixed` at 45px, the rest are `Interactive`.
- `setShowGrid(False)`, `setSortingEnabled(False)` (Qt's own sort indicator is not used — the arrow is a **suffix of the header text**, see `_header_text_with_arrow`).

**App** — `MainWindow(QMainWindow)`:
- Row 1: path entry + 浏览 + 子文件夹 checkbox + 开始检测 button (toggles to 检测中 / 正在停止... when scanning; clicking while scanning cancels via `self._cancel`)
- Row 2: 码率标准 + 采样率标准 inputs on the left; **清除记录 → 定点截图 → 下载海报** in that order at the row's right end (定点截图 was moved after 清除记录 on 2026-11-03 — it used to sit next to the kHz label; 下载海报 was added after it on 2026-11-04), then a 272px trailing spacing (the row's right margin; the rightmost button — now 下载海报 — aligns with Row 1's path entry right edge). All three buttons are kept as attributes (`self.clear_btn` / `self.snapshot_btn` / `self.poster_btn`) so the order is assertable
- Center: `QTableView` with the 12 columns above
- Bottom: 迁移地址 + 浏览 + 移动达标文件 + 移动全部文件
- Status bar: a single `QStatusBar` line. Terminal states append the tally via `set_status(text, with_counts=True)` → `检测完成：共 10 · 达标 8 · 不达标 2`; intermediate states (就绪 / 正在检测... (3/10) a.mp4) show only the progress text. The "共 N" in the tally counts **results produced**; the M in "已完成 N/M" is the **total files found** — different sources, not a contradiction.
- `<Control-o>` opens the browse-folder dialog — **the only shortcut in the app**
- Drag-and-drop: the window `setAcceptDrops(True)` and an `eventFilter` is installed on `view.viewport()` and `view.horizontalHeader()`, so the **whole table area** (header + body) accepts drops. Paths come from `event.mimeData().urls()` → `toLocalFile()`.

**Scan target precedence** — dragging files records them in `_selected_files`, and 开始检测 then scans that list instead of the path box. Editing the 文件路径 entry (by typing) clears `_selected_files` via `path_edit.textChanged`, so the next scan follows the path box without needing 清除记录 first; 浏览 and drag use `_set_path` (which sets `_suppress_path_trace`) to write the entry without triggering that. `_scan_target(directory)` returns `('files', list)` or `('dir', directory)`.

**Threading** — `ScanWorker(QObject)` lives in a `QThread` and pushes results back with four signals: `progress(str)`, `result_ready(VideoInfo)`, `finished()`, `cancelled()`. There are two modes because their `base_path` semantics differ:
- directory scan — `base_path = directory` (relative paths anchored to the picked folder)
- drag-dropped files — `base_path = os.path.dirname(files[0])` so the first file shows `./<title>` in the title column

The `self._cancel: threading.Event` is checked at the top of each per-file loop; the status bar then reports "已停止检测（已完成 N/M）" with the counts appended. Cancellation never calls `QThread.terminate()` (on Windows that hangs the whole process). If the thread has not finished when the window closes, it is **orphaned** into `MainWindow._orphan_threads` — a **class attribute** pointing at the same list as the module-level `_ORPHANED_THREADS`, so the registration stays reachable from `__del__` while the interpreter tears down (the class object is referenced by the class itself; CPython ≥ 3.4 does **not** clear module globals at teardown). A dropped reference = a running `QThread` destroyed = `abort`, exit code 134. `main()` waits for those threads inside a `try/finally` and calls `os._exit(0)` in the `finally` — never `flush()` stdout/stderr, because in a windowed build both are `None` and a flush would raise before the `os._exit`. `_on_thread_finished` deliberately **skips `deleteLater()`** for threads already in the orphan registry: a deleted C++ object makes the exit path's `thread.wait()` raise `RuntimeError`, which would skip the `os._exit`.

**Fonts** — `ui_font()` is **lazy** and must be called only after a `QApplication` exists (`QFontMetrics` without a `QGuiApplication` aborts the process with exit code 134). `apply_app_font(app)` is called from `MainWindow.__init__` as well as from `main()`, so anyone constructing `MainWindow()` directly gets the same 10pt font. Two things are **calibrated to 10pt** and must move together if the size ever changes: the main table's row height (`QFontMetrics(ui_font()).height() + 12` → 28px, deliberately not hard-coded) and `video_core.truncate_text`'s fast-path estimate (`per_char = 13` ASCII / `16` CJK — see the comment there; Qt's elide is off, so an estimate that is too small overflows the column). The ASCII constant is the **widest printable ASCII** at 10pt (`W` = 13px), not the average — an average-sized estimate lets wide-glyph text through the fast path unmeasured.

**File mover** — both `_move_passing_files` and `_move_all_files` follow the same shape: confirm via a `QMessageBox` question, `os.makedirs(dest_dir, exist_ok=True)`, then `shutil.move` each file, with `_get_unique_dest` appending `_1`, `_2`, ... on name collisions. `_move_passing_files` filters `video_results` by `is_passing`; `_move_all_files` moves everything regardless of pass/fail. Both are **moves**, not copies, and they walk `video_results` — the **insertion order** list, not the table's sorted order.

**Output directories are created on demand** — scanning/browsing never creates a directory. `snapshot` (screenshots) appears when you click 开始截图 in the dialog; `checked` (moved files) appears when you click 移动达标文件 / 移动全部文件; the poster folder appears when you click 开始下载 in the poster dialog (that dialog's **only** `os.makedirs` call site). The screenshot dialog never calls `os.makedirs` itself — it goes through `MainWindow.ensure_snapshot_dir()`, the single gate for that rule (**查看已有截图 does not create it either** — that button only reads; a missing dir produces a status line). Two fields carry the paths, both derived from the source folder and overwritten on every scan (`_sync_derived_paths`):
- 迁移地址 → `checked_dest_for(parent)` = `<parent>\checked` (backslashes **unconditionally**)
- 截图地址 → `snapshot_dest_for(parent)` = `<parent>\snapshot` (backslashes **only on win32** — deliberate asymmetry, see `docs/产品文档.md` §6 #3)

The 浏览 button picks a **parent** folder and appends the fixed subfolder (`F\snapshot`, `F\checked`); typing a path into the field uses it verbatim.

**Snapshot dialog** — `SnapshotDialog(QDialog)` (modal) with `SnapshotGridModel` + `SnapshotDelegate` + `EnlargeDialog`. The grid is a `QTableView` (rows = files, columns = timestamps) painting `QPixmap` thumbnails via a delegate — not a `QTableWidget` of labels, so the grid area holds no child windows. Only the **320px** thumbnail stays in memory; the 640px original is re-loaded **from disk** by the zoom window. The grid's row height is **`SNAPSHOT_ROW_H` (188px = 180 + 8)** so the 320×180 thumbnail is drawn at its natural size — the delegate scales the pixmap to fit the cell (`KeepAspectRatio`), so a short row crushes it (this was a real bug: Qt's default 30px row rendered it at 53×30). Extraction runs on a **plain daemon thread** (not a `QThread`) so the dialog can be closed mid-run without `abort`; the cancel flag is only checked between cells, so an already-running ffmpeg is not killed.

**Two snapshot buttons** — the dialog's top row carries 查看已有截图 and 开始截图, and they are deliberately **different jobs**:
- **开始截图** extracts frames and **overwrites** the files on disk. Unchanged since 2026-10-27.
- **查看已有截图** (`_on_view_existing`) is **read-only**: it scans the snapshot dir for filenames matching the writer's rule, matches them against the current selection, and back-fills the grid. It never extracts, never writes, and **never creates the directory** (a missing dir only produces a status line). The **timepoint columns come from the directory** (whatever timepoints exist on disk, sorted), *not* from the input boxes — the input boxes belong to 开始截图 and are left untouched.
- The scan + decode runs in a **background thread** (`_existing_worker`, mirroring `_snapshot_worker`): the worker parses filenames, then decodes each PNG into a **QImage** (reentrant → safe off the GUI thread) and hands it to the main thread, which builds the **QPixmap** (`QPixmap` may only be created on the GUI thread). The queue is **bounded (maxsize=20)** so a slow main thread applies back-pressure instead of letting decoded images pile up in RAM. The bottom status line reports `分析中 N/M 个文件` → `加载中 K/T (视频名)` → `已有截图: …`; both buttons are disabled while it runs.
- The match key is `os.path.splitext(info.title)[0]` — the *same* string the writer uses (`VideoInfo.title` is the ffprobe metadata title, falling back to the filename stem), so the two sides agree by construction.
- A cell with no match gets `CellState(status='empty')` → the delegate paints **nothing**. `'empty'` is distinct from `'idle'` ('…', "not extracted yet") — that distinction is the point of the feature.
- Same (video, timepoint) twice on disk → the newer mtime wins.

**Poster dialog** — `PosterDialog(QDialog)`, opened from the main window's 下载海报 button. It reads a **spreadsheet of poster links** and downloads the images; it is **independent of the video scan** (no `video_results` needed).
- **Modeless** (`setModal(False)` + `show()`, not `exec()`): 「下载海报」and 「视频检测」are two **separate features** that merely share the main window. With the poster dialog open the main window still scans, sorts and moves files. The main window keeps the dialog in `self._poster_dialog` (a modeless dialog must be referenced or Python GC takes it away) and re-raises the existing one instead of opening a second. `finished(int)` → `_on_poster_dialog_finished(result=0)` — the slot **must** accept the int, or PySide raises and the reference is never cleared.
- **Zero third-party dependencies**: `.csv` via the stdlib `csv` module (encoding sniffed utf-8-sig → gbk → utf-8); `.xlsx` via a hand-written parser (`zipfile` + `ElementTree`: `xl/workbook.xml` → first sheet's `r:id` → `xl/_rels/workbook.xml.rels` → the sheet part; strings from `xl/sharedStrings.xml` or `inlineStr`; cell refs like `A1`/`AA1` map to column indexes). **Old binary `.xls` is not supported** — `read_tabular_document` raises `DocumentFormatError` telling the user to re-save as `.xlsx`/`.csv`. Rationale: the project has exactly one third-party dep (PySide6); adding openpyxl/xlrd would touch `build.bat`'s `pip install` and the PyInstaller bundle, and the packaged exe can only be verified on Windows.
- **Field guessing** is pure and testable: `guess_link_columns` returns **every** column that looks like a poster link (header-name hint **and** the values really look like URLs → else just the values → else *nothing*, so the user picks instead of the app silently downloading the wrong column); `guess_clipname_column` picks the **folder-name** column (header hints → a column whose value is the same on every row → first column); `guess_partname_column` picks the **file-name** column and **requires its values to differ per row** (header hints + varies → any varying non-link column → the clipname column). The UI shows **all columns as a row of checkboxes** (auto-checked ones are the detected ones) — a table can have several poster-link columns (竖版/横版…), so a single-select combo would not do.
- **Naming** — `render_filename(template, partname, imgcol)` substitutes `{partname}`/`{imgcol}` (and the `[partname]`/`[imgcol]` bracket spelling), then replaces the illegal filename chars `\ / : * ? " < > |` with `_`. `{imgcol}` is that link column's **name**, not its value — with several link columns each file gets its own column name (`星穹计划-竖版海报.jpg` / `星穹计划-横版海报.jpg`).
- **Two different sources** (2026-11-04): the **命名来源** combo (user-visible) feeds `{clipname}` → only the **folder** name. The `{partname}` (file name) column is **auto-picked** — no combo (the user asked to drop it): name-ish header **and** values differ per row → else a varying **text** (not numeric) non-link column → else any varying column. It must not be the same column as clipname: a clip name repeats on every row, so using it for file names made every poster land on the same file (later rows overwrote earlier ones). The picked column is reported in the status line (`文件名取自「集名」`).
- **Extension** — `extension_for_url(url, content_type)`: URL path suffix → `Content-Type` → `.jpg`.
- **Duplicate names** — `unique_poster_path(output_dir, base_name, ext, taken)` picks the first free `name.ext` / `name_1.ext` / `name_2.ext` (no chained `a_1_1` suffixes) and **claims** the name in `taken`. A name is "taken" if it is on disk **or** another thread in the same batch claimed it — so the check + claim must happen under one lock (`download_poster(..., taken=..., lock=...)` takes the lock; the network fetch stays outside it, so the 3 downloads really run in parallel).
- **Dialog layout** (2026-11-04, user-specified order): row 1 = 文档 + 浏览 + **开始分析**; row 2 = **命名来源** (combo, 160px wide) + **海报地址** + 浏览; row 3 = **海报命名规则** (template) + 「首行为字段名」 + 开始下载; row 4 = **海报链接字段** (a row of checkboxes, 40px tall, on its own row). Changing 命名来源 **recomputes 海报地址** (`_sync_out_dir(force=True)`) — the two are meant to move together.
- **The download button has three states** (2026-11-04): `开始下载` → `下载中` → (click) `继续下载` → (click) `下载中` → done → `开始下载`. Pausing is **between files**: the in-flight download finishes, no new one starts. The worker waits on a `threading.Event` between jobs (`_pause`); the cancel event still wins.
- The download runs on **plain daemon threads** (`_poster_worker`, same family as `_snapshot_worker`): one **job queue** + **`POSTER_PARALLEL` = 3 worker threads** pulling from it (2026-11-04: serial → 3 in parallel). The counters are shared under a `threading.Lock`; the message queue is **bounded (maxsize=20)** so a slow main thread applies back-pressure; the worker blocks on `put(timeout=…)` so a closed dialog can't wedge it forever. Closing the dialog cancels (the in-flight request is *not* killed). One failed row does not abort the batch; the summary is `下载完成：成功 N · 失败 M` plus the first 3 errors.
- Changing the document path or the 「首行为字段名」 checkbox **invalidates the analysis** (the download button greys out) — otherwise a stale document would be downloaded.


## Key design notes

- `get_ffprobe_path()` and `get_ffmpeg_path()` are wrapped in `@lru_cache(maxsize=1)` — the lookup happens once per process
- Relative paths are displayed as `./` for the scan root and `./A/B` for subfolders, with backslashes normalized to forward slashes for cross-platform consistency
- The relative path is concatenated with the file title in the first column (see `VideoTableModel._full_text`, key `title`)
- Title metadata is read from `format.tags` with case-insensitive key matching, falling back to the filename without extension
- The `scanning` flag + button state prevent re-entrant scans; drag-drop is also blocked while scanning (an info dialog is shown and the drop ignored)
- Container format: prefer the `format_name` token matching the file extension (e.g. an `.mp4` file with `format_name='mov,mp4,...'` resolves to `'mp4'`); fall back to the first token, then the extension, then `'N/A'`
- `DEFAULT_BITRATE_KBPS = 30000`, `DEFAULT_SAMPLE_RATE_KHZ = 48` — both editable from the UI; values are parsed with `parse_threshold()` and must be positive and finite
- Window: `1440x700`, `minsize(1000, 500)`, title `"视频检测v4.2"` (1440 = the sum of column widths 1347px fits without a horizontal scrollbar; a 1366px laptop screen does not fit the window — deliberate trade-off)
- `video_checker.py` uses **CRLF** line endings. Never rewrite it with a plain-LF write — use the Edit tool for precise replacements, and verify with `python3 -c "d=open('video_checker.py','rb').read(); c=d.count(b'\r\n'); print('bare LF:', d.count(b'\n')-c)"` (must be `0`).
