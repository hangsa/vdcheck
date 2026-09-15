# Per-Cell Fail Coloring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ttk.Treeview` with a custom `VideoGridView` so failing fields (bitrate / sample_rate) + title + result turn red while other cells keep their normal color; passing rows stay fully green.

**Architecture:** Single-file change to `video_checker.py`. New `VideoGridView` class built on `tk.Canvas` + `ttk.Frame` + `tk.Label` gives per-cell foreground control that Treeview's row-level tags cannot. `VideoInfo` gains two threshold fields so color logic can run inside the view without extra parameters. A module-level `_cell_fg(info, key)` helper keeps the color rule table in one place.

**Tech Stack:** Python 3 stdlib, Tkinter (`tk`, `tkinter.ttk`), `tkinterdnd2` (already used). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-15-per-cell-fail-coloring-design.md`

**Note on TDD:** Project has no test framework. Spec explicitly excludes automated tests. Each step uses a manual smoke check (`python -c "..."` or app run) instead of a pytest step. Pure logic helpers are kept pure precisely so they could be unit-tested later if a framework is introduced.

---

## File Changes

- Modify: `video_checker.py` (single file; no new files)
  - Add `bitrate_std`, `sample_rate_std` fields to `VideoInfo` dataclass
  - Update `parse_video_info` to populate the new fields
  - Add module-level `_cell_fg(info, key)` helper
  - Add `VideoGridView` class
  - Replace `ttk.Treeview` usage in `VideoCheckerApp` (creation, clear, add, drag-drop)
  - Remove obsolete `tag_configure('pass' / 'fail')` calls

---

### Task 1: Extend VideoInfo dataclass and parse_video_info

**Files:**
- Modify: `video_checker.py:42-56` (dataclass)
- Modify: `video_checker.py:213-227` (return VideoInfo in parse_video_info)

- [ ] **Step 1: Add two new fields to the VideoInfo dataclass**

Edit `video_checker.py` lines 42-56. Insert two fields after `is_passing` and before `full_path`:

```python
@dataclass
class VideoInfo:
    rel_path: str
    title: str
    resolution: str
    frame_rate: str
    bitrate_kbps: float
    video_codec: str
    audio_codec: str
    audio_channels: str
    audio_sample_rate: str
    duration: str
    file_size: str
    is_passing: bool
    bitrate_std: float          # NEW: 码率阈值（kbps），扫描时写入
    sample_rate_std: float      # NEW: 采样率阈值（kHz），扫描时写入
    full_path: str
```

The new fields go before `full_path` to keep grouped display fields together (matching the existing layout convention).

- [ ] **Step 2: Update parse_video_info to write the new fields**

Edit `video_checker.py` lines 213-227 (the `return VideoInfo(...)` block). Add the two new keyword arguments:

```python
    return VideoInfo(
        rel_path=rel_path,
        title=title,
        resolution=resolution,
        frame_rate=frame_rate,
        bitrate_kbps=bitrate_kbps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        audio_sample_rate=audio_sample_rate,
        duration=duration,
        file_size=file_size,
        is_passing=is_passing,
        bitrate_std=bitrate_std,
        sample_rate_std=sample_rate_std,
        full_path=full_path,
    )
```

- [ ] **Step 3: Smoke-check the data layer**

Run:

```bash
python -c "
from video_checker import VideoInfo, parse_video_info

# Minimal ffprobe-shaped dict that exercises both pass/fail branches
data = {
    'streams': [
        {'codec_type': 'video', 'width': 1920, 'height': 1080,
         'r_frame_rate': '30000/1001', 'bit_rate': '35000000',
         'codec_name': 'h264'},
        {'codec_type': 'audio', 'codec_name': 'aac',
         'channels': 2, 'sample_rate': '48000'},
    ],
    'format': {'duration': '120.0', 'size': '1000000',
               'tags': {'title': 'demo'}, 'bit_rate': '35000000'},
}

info = parse_video_info(data, '/tmp/x.mp4', '/tmp', 30000.0, 48.0)
assert info is not None
assert info.bitrate_std == 30000.0, f'got {info.bitrate_std}'
assert info.sample_rate_std == 48.0, f'got {info.sample_rate_std}'
assert info.is_passing is True, f'expected pass, got {info.is_passing}'

# Now flip bitrate below threshold
data['streams'][0]['bit_rate'] = '10000000'
info = parse_video_info(data, '/tmp/x.mp4', '/tmp', 30000.0, 48.0)
assert info.is_passing is False
assert info.bitrate_kbps == 10000.0

print('OK')
"
```

Expected: `OK` on the last line. Any `AssertionError` indicates a regression in the dataclass or parse_video_info.

- [ ] **Step 4: Commit**

```bash
git add video_checker.py
git commit -m "feat(data): VideoInfo 携带码率/采样率阈值

为 VideoGridView.add_row 提供独立判定每个 cell 颜色所需的阈值。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Add module-level _cell_fg helper

**Files:**
- Modify: `video_checker.py` (insert after parse_video_info, around line 228)

- [ ] **Step 1: Insert the helper function**

After the closing of `parse_video_info` (line 227), insert:

```python
def _cell_fg(info: VideoInfo, key: str) -> str | None:
    """决定一个 cell 的前景色。None 表示用系统默认色。"""
    if info.is_passing:
        return '#228B22'

    failing = {'title', 'result'}
    if info.bitrate_kbps < info.bitrate_std:
        failing.add('bitrate')
    if not info.sample_rate_passing:
        failing.add('audio_sample_rate')

    return '#DC143C' if key in failing else None
```

The function is module-level (not a method) so the color rule table lives in one obvious place and can later be unit-tested by importing the module.

- [ ] **Step 2: Smoke-check the color helper**

Run:

```bash
python -c "
from video_checker import VideoInfo, _cell_fg

base = dict(
    rel_path='./', title='x', resolution='1920x1080', frame_rate='30.00 fps',
    bitrate_kbps=35000.0, video_codec='h264', audio_codec='aac',
    audio_channels='2', audio_sample_rate='48.0 kHz', duration='00:02:00',
    file_size='1.0 MB', full_path='/tmp/x.mp4',
)

# 全部达标：所有 cell 绿
i = VideoInfo(is_passing=True, bitrate_std=30000.0, sample_rate_std=48.0, **base)
assert _cell_fg(i, 'title') == '#228B22'
assert _cell_fg(i, 'bitrate') == '#228B22'
assert _cell_fg(i, 'audio_sample_rate') == '#228B22'
assert _cell_fg(i, 'result') == '#228B22'
assert _cell_fg(i, 'resolution') == '#228B22'

# 仅码率不达标
i = VideoInfo(is_passing=False, bitrate_std=30000.0, sample_rate_std=48.0,
              bitrate_kbps=10000.0, **{k: v for k, v in base.items() if k != 'bitrate_kbps'})
# 简化构造：直接覆盖
i = VideoInfo(is_passing=False, bitrate_std=30000.0, sample_rate_std=48.0, **base) | {'bitrate_kbps': 10000.0} if False else None
# 用 dataclasses.replace 改更清晰
from dataclasses import replace
i = replace(i, bitrate_kbps=10000.0, is_passing=False)
assert _cell_fg(i, 'title') == '#DC143C'
assert _cell_fg(i, 'bitrate') == '#DC143C'
assert _cell_fg(i, 'result') == '#DC143C'
assert _cell_fg(i, 'audio_sample_rate') is None
assert _cell_fg(i, 'resolution') is None

print('OK')
"
```

Expected: `OK` on the last line. Note: the test re-uses `i` after the `replace` — if that looks awkward in review, simplify by rebuilding `i` for each scenario. The check that matters is that `bitrate_kbps < bitrate_std` lights up `title/result/bitrate` red while leaving `resolution/audio_sample_rate` at default.

If `replace` or `_cell_fg` is missing, the assertion will fail with `AttributeError` or `NameError`, signaling a missing edit.

- [ ] **Step 3: Commit**

```bash
git add video_checker.py
git commit -m "feat(color): 添加 _cell_fg(info, key) 判定函数

把颜色规则表收敛到一处：达标全绿；不达标时仅 title/result/不达标字段红。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Build VideoGridView class

**Files:**
- Modify: `video_checker.py` (insert as new class after `_cell_fg`, around line 246)

This is the largest task. The class is broken into build sub-steps so each commit keeps the file in a runnable state.

- [ ] **Step 1: Add class skeleton with constants and __init__**

Insert after `_cell_fg`:

```python
class VideoGridView:
    """自绘的可滚动表格，每个 cell 独立控制 fg。"""

    FG_PASS = '#228B22'
    FG_FAIL = '#DC143C'

    def __init__(self, parent: tk.Widget, columns: dict[str, tuple[str, int]]):
        """
        parent: 父容器（ttk.Frame）
        columns: {'key': ('中文表头', width_in_pixels)}
        """
        self.parent = parent
        self.columns = columns
        self._rows: list[ttk.Frame] = []
        self._build()

    def _build(self):
        """构建整个组件。"""
        # 主容器
        self.outer = ttk.Frame(self.parent)

        # 表头
        self._build_header()

        # 滚动区
        body = ttk.Frame(self.outer)
        body.pack(side='top', fill='both', expand=True)

        self.vsb = ttk.Scrollbar(body, orient='vertical')
        self.vsb.pack(side='right', fill='y')
        self.hsb = ttk.Scrollbar(body, orient='horizontal')
        self.hsb.pack(side='bottom', fill='x')

        self.canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.vsb.config(command=self.canvas.yview)
        self.hsb.config(command=self.canvas.xview)
        self.canvas.config(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        self.data_frame = ttk.Frame(self.canvas)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self.data_frame, anchor='nw'
        )
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        self.data_frame.bind('<Configure>', self._on_data_configure)
```

- [ ] **Step 2: Add _build_header and _column_anchor**

Continue inside the class (insert after `_build`):

```python
    def _build_header(self):
        self.header_frame = ttk.Frame(self.outer)
        self.header_frame.pack(side='top', fill='x')
        for i, (key, (heading, width)) in enumerate(self.columns.items()):
            anchor = self._column_anchor(key)
            lbl = tk.Label(
                self.header_frame,
                text=heading,
                font=('TkDefaultFont', 9, 'bold'),
                anchor=anchor,
                padx=4, pady=2,
                relief='raised', bd=1,
            )
            lbl.grid(row=0, column=i, sticky='nsew')
            self.header_frame.grid_columnconfigure(i, minsize=width)

    @staticmethod
    def _column_anchor(key: str) -> str:
        if key == 'title':
            return 'w'
        if key in ('bitrate', 'audio_sample_rate', 'file_size'):
            return 'e'
        return 'center'
```

- [ ] **Step 3: Add scroll bindings**

Continue inside the class:

```python
    def _on_canvas_configure(self, event):
        # 让 data_frame 宽度等于 canvas 可见宽度
        self.canvas.itemconfigure(self._canvas_window, width=event.width)

    def _on_data_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
```

- [ ] **Step 4: Add mouse wheel bindings**

Continue inside the class. Bind at the canvas level (not bind_all, to avoid hijacking the entire window):

```python
    def _bind_mousewheel(self):
        self.canvas.bind('<Enter>', lambda _e: self._wheel_bind_id())
        self.canvas.bind('<Leave>', lambda _e: self.canvas.unbind_all('<MouseWheel>'))

    def _wheel_bind_id(self):
        # macOS / Windows
        self.canvas.bind_all('<MouseWheel>', self._on_mousewheel)
        # Linux (X11)
        self.canvas.bind_all('<Button-4>', self._on_mousewheel_linux)
        self.canvas.bind_all('<Button-5>', self._on_mousewheel_linux)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def _on_mousewheel_linux(self, event):
        delta = -1 if event.num == 4 else 1
        self.canvas.yview_scroll(delta, 'units')
```

Wire the binding by adding `self._bind_mousewheel()` as the last line of `_build()` (right after the `<Configure>` bindings).

- [ ] **Step 5: Add clear and add_row**

Continue inside the class:

```python
    def clear(self):
        """清空所有数据行，滚动条复位。"""
        for child in self.data_frame.winfo_children():
            child.destroy()
        self._rows.clear()
        self.canvas.yview_moveto(0)

    def add_row(self, info: VideoInfo):
        """往表格里追加一行，cell 颜色由 _cell_fg 决定。"""
        row_frame = ttk.Frame(self.data_frame)
        row_frame.pack(fill='x')
        self._rows.append(row_frame)

        for i, (key, (_heading, width)) in enumerate(self.columns.items()):
            text = self._format_cell(info, key)
            anchor = self._column_anchor(key)
            fg = _cell_fg(info, key)
            cell = tk.Label(
                row_frame,
                text=text,
                anchor=anchor,
                padx=4, pady=2,
                fg=fg if fg is not None else 'black',
            )
            cell.grid(row=0, column=i, sticky='nsew')
            row_frame.grid_columnconfigure(i, minsize=width)

    @staticmethod
    def _format_cell(info: VideoInfo, key: str) -> str:
        """把 VideoInfo 字段映射成 cell 显示文本。"""
        if key == 'title':
            rel = info.rel_path.rstrip('/')
            return f"{rel}/{info.title}" if rel != '.' else f"./{info.title}"
        if key == 'bitrate':
            return f"{info.bitrate_kbps:.0f}"
        if key == 'result':
            return "达标" if info.is_passing else "不达标"
        return {
            'resolution': info.resolution,
            'frame_rate': info.frame_rate,
            'video_codec': info.video_codec,
            'audio_codec': info.audio_codec,
            'audio_channels': info.audio_channels,
            'audio_sample_rate': info.audio_sample_rate,
            'duration': info.duration,
            'file_size': info.file_size,
        }[key]
```

Note on `fg`: `tk.Label` does not accept `None` for `fg` in all Tk versions; passing `'black'` (the standard default) is safer than `None`. The visual result is the same as "no fg set" on default-themed widgets.

- [ ] **Step 6: Add bind_drop**

Continue inside the class:

```python
    def bind_drop(self, callback):
        """把拖拽事件绑定到 data_frame。"""
        self.data_frame.drop_target_register('DND_Files')
        self.data_frame.dnd_bind('<<Drop>>', callback)
```

This assumes `tkinterdnd2.TkinterDnD` has been mixed into Tk (the existing `main()` already does this with `TkinterDnD.Tk()`).

- [ ] **Step 7: Smoke-check VideoGridView instantiation**

Run:

```bash
python -c "
import tkinter as tk
from tkinter import ttk
from video_checker import VideoGridView, VideoInfo

# 最小化 columns dict（用真实列名以便 add_row 不抛 KeyError）
columns = {
    'title': ('标题', 350), 'resolution': ('分辨率', 90),
    'frame_rate': ('帧率', 80), 'bitrate': ('码率', 90),
    'video_codec': ('视频编码', 80), 'audio_codec': ('音频编码', 80),
    'audio_channels': ('声道', 55), 'audio_sample_rate': ('采样率', 90),
    'duration': ('时长', 75), 'file_size': ('大小', 80), 'result': ('结果', 70),
}

root = tk.Tk()
grid = VideoGridView(root, columns)
info = VideoInfo(
    rel_path='./', title='demo', resolution='1920x1080', frame_rate='30.00 fps',
    bitrate_kbps=35000.0, video_codec='h264', audio_codec='aac',
    audio_channels='2', audio_sample_rate='48.0 kHz', duration='00:02:00',
    file_size='1.0 MB', is_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
    full_path='/tmp/demo.mp4',
)
grid.add_row(info)
grid.update_idletasks()
grid.clear()
root.destroy()
print('OK')
"
```

Expected: `OK`. The `clear()` call at the end exercises the destroy path; `update_idletasks()` forces the geometry calculations to actually run so any grid-column misconfig would surface immediately.

If the script raises `KeyError` from `_format_cell`, you forgot a column. If it raises `TclError` from grid, a column index is out of range.

- [ ] **Step 8: Commit**

```bash
git add video_checker.py
git commit -m "feat(ui): 新增 VideoGridView（自绘可滚动表格）

Canvas + Frame + Label，每个 cell 独立设 fg；保留鼠标滚轮与拖拽入口。
暂未接入 VideoCheckerApp。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Wire VideoGridView into VideoCheckerApp

**Files:**
- Modify: `video_checker.py` (multiple sites in `VideoCheckerApp`)

- [ ] **Step 1: Replace tree creation in _create_widgets**

Find the Treeview block in `_create_widgets` (lines ~298-345 of the original file, the `columns = (...)` through `tag_configure('fail'...)` block). Replace it with:

```python
        # === Main: 结果表格（自绘 VideoGridView） ===
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill='both', expand=True, padx=5, pady=5)

        columns = (
            'title', 'resolution', 'frame_rate', 'bitrate',
            'video_codec', 'audio_codec', 'audio_channels', 'audio_sample_rate',
            'duration', 'file_size', 'result',
        )
        headers = {
            'title': ('标题', 350),
            'resolution': ('分辨率', 90),
            'frame_rate': ('帧率', 80),
            'bitrate': ('码率(kbps)', 90),
            'video_codec': ('视频编码', 80),
            'audio_codec': ('音频编码', 80),
            'audio_channels': ('声道数', 55),
            'audio_sample_rate': ('采样率(kHz)', 90),
            'duration': ('时长', 75),
            'file_size': ('文件大小', 80),
            'result': ('结果', 70),
        }

        self.grid = VideoGridView(tree_frame, headers)
        self.grid.outer.pack(fill='both', expand=True)
```

Delete the now-unused Treeview widget construction (including the `ttk.Scrollbar` pair and `tag_configure` calls).

- [ ] **Step 2: Update _add_result**

Replace the body of `_add_result` (lines ~471-494 of original) with:

```python
    def _add_result(self, info: VideoInfo):
        """添加一条结果到表格"""
        self.video_results.append(info)
        self.grid.add_row(info)
```

The display-text formatting moves into `VideoGridView._format_cell` (Task 3).

- [ ] **Step 3: Update the clear loops in _start_scan and _on_file_drop**

In `_start_scan` (around line 418-419), replace:

```python
        # 清空表格
        for item in self.tree.get_children():
            self.tree.delete(item)
```

with:

```python
        # 清空表格
        self.grid.clear()
```

In `_on_file_drop` (around line 639-640), apply the same change:

```python
        self.video_results.clear()
        self.grid.clear()
```

(removing the `for item in self.tree.get_children(): self.tree.delete(item)` lines).

- [ ] **Step 4: Update drag-and-drop wiring**

In `_create_widgets`, find:

```python
        # 启用拖拽文件到表格
        self.tree.drop_target_register('DND_Files')
        self.tree.dnd_bind('<<Drop>>', self._on_file_drop)
```

Replace with:

```python
        # 启用拖拽文件到表格
        self.grid.bind_drop(self._on_file_drop)
```

`bind_drop` was added in Task 3 Step 6.

- [ ] **Step 5: Smoke-check full app startup**

Run:

```bash
python -c "
import tkinter as tk
from video_checker import VideoCheckerApp
from tkinterdnd2 import TkinterDnD

root = TkinterDnD.Tk()
app = VideoCheckerApp(root)
root.update_idletasks()
# 手动塞一行，验证 add_row 没崩
from dataclasses import replace
from video_checker import VideoInfo
info = VideoInfo(
    rel_path='./', title='t', resolution='r', frame_rate='f',
    bitrate_kbps=100.0, video_codec='v', audio_codec='a',
    audio_channels='2', audio_sample_rate='48.0 kHz', duration='00:00:01',
    file_size='1.0 MB', is_passing=False, bitrate_std=30000.0, sample_rate_std=48.0,
    full_path='/tmp/t.mp4',
)
app.grid.add_row(info)
root.update_idletasks()
app.grid.clear()
root.destroy()
print('OK')
"
```

Expected: `OK`. The script imports the app, constructs the full window (which is the most likely place for missing-attribute / wrong-method-name errors to surface), and exercises both `add_row` and `clear` paths.

- [ ] **Step 6: Commit**

```bash
git add video_checker.py
git commit -m "refactor(app): 用 VideoGridView 替换 ttk.Treeview

- 删除 tag_configure 与相关 tree 构造
- _add_result / 清空逻辑 / 拖拽绑定 全部委托给 grid

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Manual end-to-end verification

This task is a checklist; not strictly code, but each step requires the previous to pass. Fix regressions by amending into Task 4 (or as a follow-up commit if discovered later).

**Files:** none modified unless regressions found.

- [ ] **Step 1: Prepare test fixture**

Build (or reuse) a directory containing 4 videos:
- `pass.mp4` — bitrate ≥ 30000 kbps, sample_rate ≥ 48 kHz
- `fail_bitrate.mp4` — bitrate < 30000 kbps, sample_rate ≥ 48 kHz
- `fail_sample.mp4` — bitrate ≥ 30000 kbps, sample_rate < 48 kHz
- `fail_both.mp4` — both below thresholds

(If you don't have real fixtures, use `ffmpeg` to remux/downsample — see `docs/superpowers/specs/2026-09-15-per-cell-fail-coloring-design.md` 测试 section for hints.)

- [ ] **Step 2: Run the app and verify colors**

```bash
python video_checker.py
```

- [ ] **Step 3: Validate pass row**

Click "开始检测" against the fixture dir. For `pass.mp4`:
- All 11 cells green.
- "结果" cell shows "达标".

- [ ] **Step 4: Validate bitrate-only fail row**

For `fail_bitrate.mp4`:
- Title cell: red
- 码率 cell: red
- 采样率 cell: default (black)
- 结果 cell: red ("不达标")
- Other cells: default

- [ ] **Step 5: Validate sample-rate-only fail row**

For `fail_sample.mp4`:
- Title cell: red
- 码率 cell: default (since bitrate ≥ threshold)
- 采样率 cell: red
- 结果 cell: red ("不达标")
- Other cells: default

- [ ] **Step 6: Validate both-fail row**

For `fail_both.mp4`:
- Title / 码率 / 采样率 / 结果 all red
- Other cells default

- [ ] **Step 7: Validate drag-and-drop**

Drag the same fixtures from Finder/Explorer onto the table. Verify:
- Table clears
- Scanning starts
- Same color rules apply

- [ ] **Step 8: Validate scroll + resize**

- Add many rows (re-scan a deeper directory). Scroll via mouse wheel and scrollbar; both should work.
- Shrink window to minsize (900×500). Horizontal scrollbar should appear; columns should remain aligned.

- [ ] **Step 9: Commit any fixes**

If steps 3-8 surfaced regressions, fix them and commit. If everything passed, no commit is needed — the previous Task 4 commit stands.

```bash
# only if there are fixes
git add video_checker.py
git commit -m "fix(ui): 修复验证发现的问题

[describe what was broken and what was fixed]

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Covered in |
|---|---|
| Background / problem statement | Plan intro + Tasks 1-3 |
| Color rules table | Task 2 (helper) + Task 3 (Step 5 `_format_cell`) |
| Architecture (VideoGridView) | Task 3 |
| VideoInfo new fields | Task 1 |
| Data flow (`_add_result` change) | Task 4 Step 2 |
| Drag-and-drop migration | Task 4 Step 4 |
| Clear migration | Task 4 Step 3 |
| Remove `tag_configure` calls | Task 4 Step 1 |
| No new dependencies | Confirmed in Tech Stack |
| Out of scope (selection, sort, hover, zebra) | Not introduced in any task |
| Error handling / boundaries | Task 3 Steps 3-4 (Configure bindings, scrollregion), Task 5 Step 8 (resize) |
| Manual verification | Task 5 |

**Placeholder scan:** No TBD/TODO. All code is shown inline.

**Type consistency:** `VideoInfo.bitrate_std` and `sample_rate_std` introduced in Task 1, read in Task 2 (`_cell_fg`) and Task 3 (`add_row`). `VideoGridView` interface (`__init__`, `clear`, `add_row`, `bind_drop`) defined in Task 3 and consumed in Task 4. `_cell_fg` defined in Task 2 and consumed in Task 3. `columns` dict shape matches the existing `headers` dict from `VideoCheckerApp._create_widgets` (used in Task 4 Step 1).

**Ambiguity check:**
- `fg='black'` for default-colored cells (Task 3 Step 5) — explicit; the visual result matches the previous Treeview default.
- `width` for headers is reused from the original `headers` dict (Task 4 Step 1) — no reinterpretation.
- Mouse wheel binding strategy (`<Enter>` / `<Leave>`) chosen to scope wheel events to the canvas area; documented inline in Step 4.

No issues to fix inline.
