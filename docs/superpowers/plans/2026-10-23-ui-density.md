# UI 信息展示效率优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉定点截图弹窗的三处信息不可达/信息丢失缺陷，合并主表格三列低密度信息以释放横向空间，统一单位显示规则，增加常驻计数与截断文本悬停提示。

**Architecture:** 单文件 Tkinter 应用（`video_checker.py`）。所有改动分两类：**(a) 抽出模块级纯函数**（`format_title` / `truncate_text`）供主网格与截图弹窗共用，消除两处重复且行为不一致的标题逻辑；**(b) 就地修改**现有 widget 构建代码。虚拟滚动的几何逻辑全部由 `len(self.columns)` 驱动，列合并不需要改任何结构性代码。

**Tech Stack:** Python 3.14 / Tkinter (tk + ttk) / tkfont / tkinterdnd2 / ffmpeg。无 pytest——测试用一次性断言脚本（`check()` + 退出码模式）。

**Spec:** `docs/superpowers/specs/2026-10-23-ui-density-design.md`

## Global Constraints

- **`video_checker.py` 是 CRLF 行尾。** 所有对该文件的编辑必须用 Edit 工具做**精确字符串替换**。**严禁**用 Python `open().read()` / `open().write()` 改它——那会把整个文件 CRLF 转成 LF，产生 2000+ 行的假 diff。改完跑 `git diff --stat`，若改动行数远超预期，说明行尾被转换了。
- **新建的断言脚本用 LF 行尾**（与现有 6 个断言脚本一致）。
- 测试方式：`python docs/superpowers/specs/<date>-<topic>-assertions.py`，退出码 0 = 通过。本项目**没有 pytest**。
- 现有 6 个断言脚本**每个任务结束时都要全跑一遍**作为回归。唯一允许修改的是 `2026-09-16-bug-audit-assertions.py`（Task 7 会改一行）。
- 中文注释与 UI 文案，与周围代码风格一致。UI 文案里的 `·`（间隔号）不是 `•`。
- 截图弹窗 `SnapshotProgressDialog` 与主网格共用纯函数，但**不共用 widget 代码**——两者是两套独立的 widget 树，不要试图合并。
- 提交信息用中文，末行加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

## 任务顺序说明

Task 2（横向滚动条）**必须排在 Task 4（弹窗标题补路径）之前**。Task 4 会让弹窗标题列变宽，若 hsb 还不存在，2 个时间点的情形就会开始溢出——先修 hsb 再加宽才不会出现中间破损态。

## Review Focus

以下输入/状况 spec 暗示了但没有任何任务的测试覆盖，是最可能咬人的。每条都已在对应任务里有测试钉住。

1. **跨盘符拖入文件**（Windows：D 盘文件 + C 盘 base）→ `os.path.relpath` 抛 `ValueError`，`rel_path` 变成绝对全路径。合理行为：标题显示完整路径且不重复出现文件名。→ Task 1
2. **视频无音频流** → `audio_codec='N/A'`。合理行为：规格列显示 `mp4/h264/N/A`，不是 `mp4/h264/`，不崩溃。→ Task 6
3. **`r_frame_rate` 为 `0/0` 或非法** → `frame_rate='N/A'`。合理行为：表头写「帧率(fps)」但 cell 显示 `N/A`，不能显示 `nan`。→ Task 7
4. **扫描目录里 0 个视频文件** → `_scan_complete` 走「未找到视频文件」分支。合理行为：常驻计数是 `共 0 · 达标 0 · 不达标 0`，不能是上一轮的残留值。→ Task 8
5. **所有时间点都超出视频时长** → 每个 cell 都 `extract_frame` 失败。合理行为：全部显示红 `×`，不残留半张图，`_photo_refs` 为空。→ Task 5

---

### Task 1: 抽出 `format_title` 与 `truncate_text` 模块级纯函数

**Files:**
- Create: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`（本计划所有任务共用这一个文件，逐步追加断言）
- Modify: `video_checker.py:1-14`（加 `import ntpath`）、`video_checker.py:227` 之前（插入两个新函数）、`video_checker.py:767-789`（`_truncate_text` 改为委托）

**Interfaces:**
- Consumes: 无（起点任务）
- Produces:
  - `format_title(info: VideoInfo) -> str` — 标题列文本
  - `truncate_text(text: str, col_width: int, font: tkfont.Font) -> str` — 像素级截断 + `...`
  - `VideoGridView._truncate_text(self, text: str, col_width: int) -> str` — 保留的薄委托，后续任务继续用

- [ ] **Step 1: 写失败的测试**

创建 `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`（**LF 行尾**）：

```python
"""界面信息展示效率优化 一次性断言脚本。

验证：
- format_title 全部 rel_path 形态 (含跨盘符绝对路径 / UNC);
- truncate_text 像素级截断 (ASCII / CJK / 边界);
- VideoGridView._truncate_text 委托到模块级实现且行为不变。

运行: python docs/superpowers/specs/2026-10-23-ui-density-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

import tkinter as tk
from tkinter import font as tkfont

from video_checker import VideoInfo, VideoGridView, format_title, truncate_text


_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_info(title='a.mp4', rel_path='./', **kw) -> VideoInfo:
    """构造测试用 VideoInfo (只填 format_title 用得到的字段)。"""
    base = dict(
        rel_path=rel_path, title=title, resolution='1920x1080',
        frame_rate='25.00', bitrate_kbps=32000.0, video_codec='h264',
        audio_codec='aac', audio_channels='2', audio_sample_rate='48.0',
        duration='00:01:00', file_size='1.0 GB', is_passing=True,
        sample_rate_passing=True, bitrate_std=30000.0, sample_rate_std=48.0,
        full_path='/x/a.mp4', container_format='mp4',
    )
    base.update(kw)
    return VideoInfo(**base)


def main() -> None:
    section("format_title — rel_path 各种形态")
    cases = [
        ('./',        'a.mp4', './a.mp4'),
        ('./A',       'a.mp4', './A/a.mp4'),
        ('./A/B',     'a.mp4', './A/B/a.mp4'),
        ('./A/B/',    'a.mp4', './A/B/a.mp4'),
        ('',          'a.mp4', './a.mp4'),
        ('D:\\videos\\a.mp4',  'a.mp4', 'D:\\videos\\a.mp4'),
        ('\\\\srv\\share\\a.mp4', 'a.mp4', '\\\\srv\\share\\a.mp4'),
    ]
    for rel, title, want in cases:
        got = format_title(make_info(title=title, rel_path=rel))
        check(f"rel_path={rel!r} → {want!r}", got == want, f"got={got!r}")

    section("format_title — 绝对路径不重复文件名 (跨盘符既有 bug)")
    got = format_title(make_info(rel_path='D:\\videos\\a.mp4'))
    check("绝对路径只出现一次", got.count('a.mp4') == 1, f"got={got!r}")

    root = tk.Tk()
    root.withdraw()
    font = tkfont.Font(root=root, font=('TkDefaultFont', 9))

    section("truncate_text — 像素级截断")
    short = 'ab.mp4'
    check("短文本原样返回", truncate_text(short, 250, font) == short,
          f"got={truncate_text(short, 250, font)!r}")

    long_ascii = 'a' * 200
    got = truncate_text(long_ascii, 100, font)
    check("超长 ASCII 被截断", len(got) < len(long_ascii), f"len={len(got)}")
    check("截断结果以 ... 结尾", got.endswith('...'), f"got={got[-10:]!r}")
    check("截断结果不超列宽", font.measure(got) <= 100 - 10,
          f"w={font.measure(got)}")

    long_cjk = '中文标题' * 40
    got = truncate_text(long_cjk, 100, font)
    check("超长 CJK 被截断", font.measure(got) <= 100 - 10,
          f"w={font.measure(got)}")

    got = truncate_text('anything', 5, font)
    check("列宽 < 10 → 返回 '...'", got == '...', f"got={got!r}")

    got = truncate_text('', 250, font)
    check("空串原样返回", got == '', f"got={got!r}")

    section("VideoGridView._truncate_text — 委托且行为一致")
    grid = VideoGridView(ttk.Frame(root), {'title': ('标题', 250)})
    check("方法委托给模块级 truncate_text",
          grid._truncate_text('a' * 200, 100)
          == truncate_text('a' * 200, 100, grid._cell_font))
    check("短文本不受影响", grid._truncate_text('a.mp4', 250) == 'a.mp4')

    root.destroy()

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()
```

在文件顶部补上 `from tkinter import ttk`（Step 1 代码块里 `ttk.Frame` 用到了）。

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — `ImportError: cannot import name 'format_title' from 'video_checker'`

- [ ] **Step 3: 实现 `format_title` 与 `truncate_text`**

在 `video_checker.py` 的 import 区（`:1-14`）加一行。找到：

```python
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, font as tkfont, messagebox, ttk
```

在 `import tkinter as tk` 之后加：

```python
import ntpath
```

在 `time_token_to_string` 函数（`:120-125`）之后、`_parse_drop_data`（`:128`）之前插入：

```python
def format_title(info: VideoInfo) -> str:
    """标题列文本: ./A/B/name.mp4 (相对路径 + 文件名)。

    rel_path 在跨盘符时会被 parse_video_info 设成绝对全路径
    (os.path.relpath 抛 ValueError 的分支, 见本文件 parse_video_info),
    那种情况下 rel_path 本身已含文件名, 直接返回, 不再拼 title ——
    否则会输出 "D:\\videos\\a.mp4/a.mp4" 这种路径重复。

    用 ntpath 而非 os.path: 绝对路径形态只在 Windows 产生, 但测试跑在
    macOS 上, os.path.isabs('D:\\...') 在 posix 下返回 False 会假绿。
    ntpath.isabs 对盘符路径与 UNC 路径两平台行为一致。
    """
    rel = info.rel_path.rstrip('/\\')
    if ntpath.isabs(rel):
        return rel
    if rel in ('', '.'):
        return f"./{info.title}"
    return f"{rel}/{info.title}"


def truncate_text(text: str, col_width: int, font: tkfont.Font) -> str:
    """按像素宽截断文本并加省略号。max_width = col_width - 10 (扣 padx + bd)。

    提到模块级是因为主网格和定点截图弹窗都要用同一套截断规则。

    快路径：按最宽字符估算宽度，若确定能装下就跳过 measure（绝大多数 cell 走这里）。
    慢路径：measure 验证 + 二分查找前缀长度。
    """
    max_width = max(0, col_width - 10)
    # 9pt 下 ASCII ~7px，CJK ~14px；纯 ASCII 用更小的上界可以跳过更多 measure
    has_cjk = any(ord(c) >= 0x80 for c in text)
    per_char = 7 if not has_cjk else 14
    if len(text) * per_char <= max_width:
        return text
    if font.measure(text) <= max_width:
        return text
    ellipsis = '...'
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + ellipsis) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis
```

把 `VideoGridView._truncate_text`（`:767-789`）整个方法体替换为委托：

```python
    def _truncate_text(self, text: str, col_width: int) -> str:
        """委托模块级 truncate_text（弹窗也用同一个实现）。"""
        return truncate_text(text, col_width, self._cell_font)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

- [ ] **Step 5: 跑全量回归 + 检查 diff 行数**

```bash
for f in docs/superpowers/specs/*assertions.py; do echo "--- $f"; python "$f" >/dev/null 2>&1 && echo "  OK" || echo "  FAIL"; done
git diff --stat
```

Expected: 6 个旧脚本 + 1 个新脚本全部 OK；`video_checker.py` 改动行数 < 60（若上千说明 CRLF 被转换，回滚重做）

- [ ] **Step 6: 提交**

```bash
git add video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
refactor(ui): 抽出 format_title / truncate_text 模块级纯函数

主网格和定点截图弹窗要共用同一套标题/截断逻辑, 但现在:
- 弹窗标题只写 info.title, 丢了 rel_path (子文件夹同名文件无法区分);
- 主网格 _format_cell 的 title 分支在 rel_path 为绝对路径时 (跨盘符,
  os.path.relpath 抛 ValueError) 会输出 "D:\\videos\\a.mp4/a.mp4",
  路径重复且分隔符混用。

format_title 用 ntpath.isabs 而非 os.path: 该形态只在 Windows 产生,
但断言脚本跑在 macOS, os.path.isabs 对盘符路径返回 False 会假绿。

truncate_text 提到模块级, 供后续的弹窗标题限宽使用。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: 定点截图弹窗补横向滚动条

**Files:**
- Modify: `video_checker.py:1275-1285`（`_build_table_initial`）
- Test: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`

**Interfaces:**
- Consumes: 无
- Produces: `SnapshotProgressDialog` 实例上多一个属性 `self._hsb`（`ttk.Scrollbar`，master 为 `dialog._middle`）

- [ ] **Step 1: 写失败的测试**

在断言脚本的 `main()` 里，`section("VideoGridView._truncate_text — 委托且行为一致")` 之前插入：

```python
    section("SnapshotProgressDialog — 横向滚动条")
    import tempfile
    from video_checker import SnapshotProgressDialog

    dlg = SnapshotProgressDialog(root, [], tempfile.mkdtemp())
    root.update_idletasks()
    check("弹窗有 _hsb", hasattr(dlg, '_hsb'))
    hsb = getattr(dlg, '_hsb', None)
    check("_hsb 是 Scrollbar", hsb is not None and hsb.winfo_class() == 'TScrollbar',
          f"got={hsb.winfo_class() if hsb else None!r}")
    check("_hsb.master 是 _middle (不能是 top)",
          hsb.master is dlg._middle, f"master={hsb.master!r}")
    check("_hsb 已 pack 进布局", hsb.winfo_manager() != '',
          f"manager={hsb.winfo_manager()!r}")
    check("canvas 绑了 xscrollcommand", 'xscroll' in dlg.canvas.cget('xscrollcommand'))
    check("_hsb 的 command 指向 canvas.xview",
          hsb.cget('command') == dlg.canvas.xview)

    section("SnapshotProgressDialog — 空文件列表不崩")
    dlg2 = SnapshotProgressDialog(root, [], tempfile.mkdtemp())
    root.update_idletasks()
    check("空 files 建表不抛异常", len(dlg2._inner.winfo_children()) == 0,
          f"children={len(dlg2._inner.winfo_children())}")
    dlg2.top.destroy(); dlg.top.destroy()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — 「弹窗有 _hsb」为 ✗，`hasattr(dlg, '_hsb')` 返回 False

- [ ] **Step 3: 实现横向滚动条**

把 `video_checker.py:1277-1285` 这 9 行替换：

```python
        self._middle = ttk.Frame(self.top)
        self.canvas = tk.Canvas(self._middle, highlightthickness=0)
        vsb = ttk.Scrollbar(self.top, orient='vertical', command=self.canvas.yview)
        # 横向滚动条: master 必须直接是 _middle (它被 pack 进的容器),
        # 不能 pack(in_=...) 跨容器 —— Windows 上那样 widget 会显示 0 尺寸。
        self._hsb = ttk.Scrollbar(self._middle, orient='horizontal',
                                  command=self.canvas.xview)
        self.canvas.config(yscrollcommand=vsb.set, xscrollcommand=self._hsb.set)
        self._header_frame = ttk.Frame(self._middle)
        self._header_frame.pack(side='top', fill='x')
        self.canvas.pack(side='top', fill='both', expand=True)
        self._hsb.pack(side='bottom', fill='x')
        vsb.pack(side='right', fill='y')
        self._middle.pack(side='top', fill='both', expand=True)
```

`_inner` 的 `<Configure>` 绑定（`:1296-1297`）已经用 `winfo_reqwidth()` 设置 scrollregion，横向滚动所需的 scrollregion 宽度已具备，**不要改**。

- [ ] **Step 4: 运行测试确认通过**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

- [ ] **Step 5: 提交**

```bash
git add video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
fix(snapshot): 弹窗补横向滚动条 (3+ 时间点内容不可达)

内容宽度 = 序号45 + 标题250 + 时间点(320+8)×N。窗口 1200:
2 个时间点 = 951 (够), 3 个 = 1279 (溢出 79), 4 个 = 1607 (溢出 407)。
超出部分被裁掉, 且弹窗没有 hsb 也没有 shift+wheel —— 用户加到第 3 个
时间点时右边的图直接消失, 没有任何方式能滚过去。

_hsb 的 master 必须是 _middle 本身, 不能 pack(in_=...) 跨容器。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: 主网格标题改用 `format_title`

**Files:**
- Modify: `video_checker.py:1020-1022`（`_format_cell` 的 title 分支）
- Test: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`

**Interfaces:**
- Consumes: `format_title(info: VideoInfo) -> str`（Task 1）
- Produces: 无新接口

- [ ] **Step 1: 写失败的测试**

在断言脚本 `main()` 里，`section("format_title — 绝对路径不重复文件名 (跨盘符既有 bug)")` 之后插入：

```python
    section("_format_cell — title 分支委托 format_title")
    for rel, title, want in cases:
        info = make_info(title=title, rel_path=rel)
        got = VideoGridView._format_cell(info, 'title')
        check(f"_format_cell rel_path={rel!r} → {want!r}", got == want,
              f"got={got!r}")
```

注意 `cases` 变量在 `main()` 里已定义于第一个 section，这段代码必须在同一个函数作用域内。

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — 至少 `rel_path='D:\\videos\\a.mp4'` 与两个 UNC/空串用例为 ✗（当前实现会把 title 再拼一次）

- [ ] **Step 3: 改 `_format_cell`**

把 `video_checker.py:1020-1022`：

```python
        if key == 'title':
            rel = info.rel_path.rstrip('/')
            return f"{rel}/{info.title}" if rel != '.' else f"./{info.title}"
```

替换为：

```python
        if key == 'title':
            return format_title(info)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

- [ ] **Step 5: 提交**

```bash
git add video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
fix(grid): 标题列走 format_title, 顺带修掉跨盘符路径重复

原本 rel_path 为绝对路径时 (跨盘符) 会输出 "D:\\videos\\a.mp4/a.mp4"。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 4: 弹窗标题补相对路径并按像素截断

**Files:**
- Modify: `video_checker.py:1152-1162`（`__init__` 加 `_cell_font`）、`video_checker.py:1327-1345`（`_make_row_labels`）
- Test: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`

**Interfaces:**
- Consumes: `format_title(info) -> str`、`truncate_text(text, col_width, font) -> str`（Task 1）；`SnapshotProgressDialog._middle` / `._inner`（Task 2 建好滚动区）
- Produces: `SnapshotProgressDialog._cell_font: tkfont.Font`；每个标题 label 上带 `_full_text: str` 与 `_truncated: bool`（Task 9 的 tooltip 会读）

- [ ] **Step 1: 写失败的测试**

在断言脚本 `main()` 里，`section("SnapshotProgressDialog — 空文件列表不崩")` 之后插入：

```python
    section("SnapshotProgressDialog — 标题显示 rel_path 且按像素限宽")
    d3 = SnapshotProgressDialog(root, [make_info(title='a.mp4', rel_path='./A/B'),
                                      make_info(title='a.mp4', rel_path='./C')],
                                tempfile.mkdtemp())
    root.update_idletasks()
    tl = d3._row_title_labels
    check("第 1 行标题含子文件夹路径", tl[0].cget('text') == './A/B/a.mp4',
          f"got={tl[0].cget('text')!r}")
    check("第 2 行标题是 ./C/a.mp4", tl[1].cget('text') == './C/a.mp4',
          f"got={tl[1].cget('text')!r}")
    check("标题列宽未超过 TITLE_COL_WIDTH",
          d3._inner.grid_bbox(1, 0, 1, 1)[2] <= d3.TITLE_COL_WIDTH,
          f"w={d3._inner.grid_bbox(1, 0, 1, 1)[2]} cap={d3.TITLE_COL_WIDTH}")

    d4 = SnapshotProgressDialog(
        root, [make_info(title='b' * 200 + '.mp4', rel_path='./X')], tempfile.mkdtemp())
    root.update_idletasks()
    lbl4 = d4._row_title_labels[0]
    check("超长路径被截断", lbl4.cget('text') != lbl4._full_text,
          f"shown_len={len(lbl4.cget('text'))}")
    check("截断后以 ... 结尾", lbl4.cget('text').endswith('...'))
    check("_full_text 存的是完整值", lbl4._full_text.endswith('.mp4'))
    check("_truncated 标记为 True", lbl4._truncated is True)
    check("超长路径不撑宽标题列",
          d4._inner.grid_bbox(1, 0, 1, 1)[2] <= d4.TITLE_COL_WIDTH,
          f"w={d4._inner.grid_bbox(1, 0, 1, 1)[2]} cap={d4.TITLE_COL_WIDTH}")

    section("SnapshotProgressDialog — 短路径不标 _truncated")
    check("未截断时 _truncated=False", tl[0]._truncated is False,
          f"got={tl[0]._truncated!r}")
    check("未截断时 shown == full", tl[0].cget('text') == tl[0]._full_text)

    d3.top.destroy(); d4.top.destroy()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — 「第 1 行标题含子文件夹路径」得到 `'a.mp4'`，与期望 `'./A/B/a.mp4'` 不符

- [ ] **Step 3: 实现**

在 `SnapshotProgressDialog.__init__` 的 `self._photo_refs: list[tk.PhotoImage] = []`（`:1158`）之后加一行：

```python
        # 标题列按像素截断用 (Label(width=N) 是最小字符数不是上限, 挡不住深路径撑宽)
        self._cell_font = tkfont.Font(font=('TkDefaultFont', 9))
```

把 `_make_row_labels`（`:1327-1345`）整个方法替换为：

```python
    def _make_row_labels(self, r: int, info) -> tuple[tk.Label, tk.Label]:
        """建一行的「序号」+「标题」两个 label.

        配色对齐主界面 VideoGridView._cell_fg 的语义 — 不画整行底色,
        只用 fg 区分: 达标全绿字, 不达标标题红字。

        标题显示完整相对路径 (与主界面 format_title 一致), 再按
        TITLE_COL_WIDTH 像素截断 —— 不截的话一个深路径会把整列撑宽,
        把所有时间点列推右。该列没设 weight, 列宽 = max(minsize, 所有
        label 的 reqwidth), Label(width=28) 是最小字符数, 挡不住。
        """
        if info.is_passing:
            fg = self.FG_PASS
        else:
            fg = self.FG_FAIL
        idx_lbl = tk.Label(self._inner, text=str(r + 1), anchor='center',
                           width=5, font=('TkDefaultFont', 9),
                           padx=4, pady=2, fg=fg)
        idx_lbl.grid(row=r, column=0, sticky='nsew', padx=4, pady=2)
        full_title = format_title(info)
        shown_title = truncate_text(full_title, self.TITLE_COL_WIDTH, self._cell_font)
        title_lbl = tk.Label(self._inner, text=shown_title, anchor='w',
                             font=('TkDefaultFont', 9),
                             padx=4, pady=2, fg=fg)
        # _full_text / _truncated 供 tooltip 用 (见 Tooltip.on_motion)
        title_lbl._full_text = full_title
        title_lbl._truncated = (shown_title != full_title)
        title_lbl.grid(row=r, column=1, sticky='nsew', padx=4, pady=2)
        return idx_lbl, title_lbl
```

注意：这里**去掉了 `width=28`**。`minsize=TITLE_COL_WIDTH`（250）已经保证列不会窄于 250px，加上像素截断后列宽恒定。

- [ ] **Step 4: 运行测试确认通过**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

- [ ] **Step 5: 跑全量回归 + 提交**

```bash
for f in docs/superpowers/specs/*assertions.py; do python "$f" >/dev/null 2>&1 && echo "OK  $f" || echo "FAIL $f"; done
git add video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
fix(snapshot): 标题列补 rel_path 并按像素限宽

标题只写 info.title, 扫子文件夹时同名文件无法区分 —— 而这正是要逐帧
肉眼比对画面的场景。

该列 grid_columnconfigure(1, minsize=250) 没设 weight, 列宽 =
max(minsize, 所有 label 的 reqwidth), 所以改成完整路径后一个深路径
就能把整列撑到 400px+ 把时间点列全推右。Label(width=28) 是最小字符数
不是上限, 挡不住 —— 改用 truncate_text 按像素截断。

顺带给标题 label 记 _full_text / _truncated, 供后续 tooltip 用。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 5: 缩略图只保留 display，释放原图

**Files:**
- Modify: `video_checker.py:1572-1586`（`mark_done`）
- Test: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`

**Interfaces:**
- Consumes: 无
- Produces: `SnapshotProgressDialog._photo_refs` 语义收紧为「每个 cell 恰好一项（display）」

- [ ] **Step 1: 写失败的测试**

在断言脚本 `main()` 末尾（`root.destroy()` 之前）插入：

```python
    section("mark_done — PhotoImage 只留 display (Review Focus #2/#5)")
    d5 = SnapshotProgressDialog(root, [make_info()], tempfile.mkdtemp())
    root.update_idletasks()
    # 用 tkinter 造一张 640x360 PNG (不依赖 ffmpeg)
    big = tk.PhotoImage(width=640, height=360)
    big.put('red', to=(0, 0, 639, 359))
    png = os.path.join(tempfile.mkdtemp(), 'frame.png')
    big.write(png, format='png')
    del big

    d5._state = 'running'
    d5.mark_done(0, 0, png)
    check("_photo_refs 每 cell 恰好 1 项 (原来 2 项)",
          len(d5._photo_refs) == 1, f"len={len(d5._photo_refs)}")
    check("留下的是 320px display 不是 640px 原图",
          d5._photo_refs[0].width() == 320, f"w={d5._photo_refs[0].width()}")
    check("cell 已挂上图片", d5._cells[0][0].cget('image') is not '')
    d5.top.destroy()

    section("mark_failed — 无残留图片 (Review Focus #5)")
    d6 = SnapshotProgressDialog(root, [make_info()], tempfile.mkdtemp())
    d6._state = 'running'
    d6.mark_failed(0, 0)
    check("失败后 _photo_refs 为空", len(d6._photo_refs) == 0,
          f"len={len(d6._photo_refs)}")
    check("失败 cell 无残留 image", d6._cells[0][0].cget('image') == '',
          f"got={d6._cells[0][0].cget('image')!r}")
    check("失败计数 +1", d6._failed_count == 1, f"got={d6._failed_count}")
    d6.top.destroy()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — 「`_photo_refs` 每 cell 恰好 1 项」得 `len=2`

- [ ] **Step 3: 实现**

把 `video_checker.py:1576-1581`：

```python
            full = tk.PhotoImage(file=image_path)
            self._photo_refs.append(full)
            display = full.subsample(self.THUMB_SUBSAMPLE, self.THUMB_SUBSAMPLE)
            self._photo_refs.append(display)
            # compound='center' 必填 (tk.Label 只认 none/center/left/right/top/bottom)
            cell.config(image=display, text='', compound='center', fg='black')
```

替换为：

```python
            full = tk.PhotoImage(file=image_path)
            display = full.subsample(self.THUMB_SUBSAMPLE, self.THUMB_SUBSAMPLE)
            # 只留 display: full 仅用于这一次 subsample, EnlargeDialog 是从磁盘
            # 路径重新加载的。subsample 返回独立副本, full 出作用域即可回收 ——
            # 每 cell 省约 0.9MB, 400 cell 场景约 360MB。
            self._photo_refs.append(display)
            # compound='center' 必填 (tk.Label 只认 none/center/left/right/top/bottom)
            cell.config(image=display, text='', compound='center', fg='black')
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

- [ ] **Step 5: 提交**

```bash
git add video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
fix(snapshot): 缩略图只留 display, 不再双份驻留原图

mark_done 把 640px 原图 full 和 320px 缩略图 display 都存进
_photo_refs, 但 full 仅用于那一次 subsample —— EnlargeDialog 从磁盘
路径重新加载。Tk 按 4 字节/像素存, 640x360 约 0.9MB/张, 400 cell
多留约 360MB。

已实测 subsample 返回独立副本, full 被 GC 后 display 仍完整渲染
(label 需求尺寸 324x184 = 图片 320x180 + Label 默认 padx 1x2)。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 6: 合并「规格」列

**Files:**
- Modify: `video_checker.py:1720-1738`（App 的死代码 `columns` 元组 + `headers` dict）
- Modify: `video_checker.py:1027-1037`（`_format_cell` 的字段字典）
- Test: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`

**Interfaces:**
- Consumes: 无
- Produces: 网格列 key 从 12 个变 10 个；新增 key `spec`

- [ ] **Step 1: 写失败的测试**

在断言脚本 `main()` 里，`section("VideoGridView._truncate_text — 委托且行为一致")` 之前插入：

```python
    section("_format_cell — spec 列 (Review Focus #2)")
    spec_cases = [
        (dict(container_format='mp4', video_codec='h264', audio_codec='aac'),
         'mp4/h264/aac'),
        (dict(container_format='matroska', video_codec='h264', audio_codec='aac'),
         'matroska/h264/aac'),
        (dict(container_format='mp4', video_codec='h264', audio_codec='N/A'),
         'mp4/h264/N/A'),
        (dict(container_format='mp4', video_codec='N/A', audio_codec='N/A'),
         'mp4/N/A/N/A'),
    ]
    for kw, want in spec_cases:
        got = VideoGridView._format_cell(make_info(**kw), 'spec')
        check(f"spec{kw} → {want!r}", got == want, f"got={got!r}")

    section("列集合 — 10 列 + 四个可排序列仍存在")
    import re
    from video_checker import _SORTABLE_COLUMNS
    app_body = app_widgets_body()
    header_keys = re.findall(r"'(\w+)':\s*\('", app_body)
    check("headers 恰好 10 列", len(header_keys) == 10, f"got={len(header_keys)}: {header_keys}")
    for removed in ('container_format', 'video_codec', 'audio_codec'):
        check(f"headers 已移除 {removed}", removed not in header_keys)
    check("headers 含 spec", 'spec' in header_keys)
    for sortable in sorted(_SORTABLE_COLUMNS):
        check(f"可排序列 {sortable} 仍在 headers 中", sortable in header_keys)
    check("死代码 columns 元组已删除", not re.search(r'^\s*columns\s*=\s*\(', app_body, re.M),
          "App._create_widgets 里还有未被读取的 columns 元组")
```

并在断言脚本的 `make_info()` 之后加这个模块级 helper（Task 7 也会复用它）：

```python
def app_widgets_body() -> str:
    """读 video_checker.py 的 _create_widgets 函数体 (headers 定义在那里)。"""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), 'video_checker.py')
    src = open(path, encoding='utf-8', newline='').read()
    return src[src.index("    def _create_widgets(self):"):
               src.index("    # ---- 事件处理 ----")]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — `_format_cell(info, 'spec')` 抛 `KeyError`

- [ ] **Step 3: 实现**

把 `video_checker.py:1720-1738`（从 `columns = (` 到 `headers = {...}` 的闭合大括号）整段替换为：

```python
        # 注意: 原来这里还有一个 columns 元组, 赋值后从未被读取 (死代码) —
        # VideoGridView 只收 headers。已删除, 避免改它的人以为那是真理来源。
        headers = {
            'title': ('标题', 250),
            'resolution': ('分辨率', 90),
            'frame_rate': ('帧率', 75),
            'bitrate': ('码率(kbps)', 85),
            'spec': ('规格', 140),
            'audio_channels': ('声道数', 55),
            'audio_sample_rate': ('采样率(kHz)', 90),
            'duration': ('时长', 75),
            'file_size': ('文件大小', 75),
            'result': ('结果', 60),
        }
```

把 `_format_cell`（`:1023-1037`）改为：

```python
        if key == 'bitrate':
            return f"{info.bitrate_kbps:.0f}"
        if key == 'spec':
            return f"{info.container_format}/{info.video_codec}/{info.audio_codec}"
        if key == 'result':
            return "达标" if info.is_passing else "不达标"
        return {
            'resolution': info.resolution,
            'frame_rate': info.frame_rate,
            'audio_channels': info.audio_channels,
            'audio_sample_rate': info.audio_sample_rate,
            'duration': info.duration,
            'file_size': info.file_size,
        }[key]
```

被删掉的三个 key **不进** `headers`，所以 `_format_cell` 不会用旧 key 调用；删掉对应字典项是为了不留会误导人的死映射。

- [ ] **Step 4: 运行测试确认通过**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

- [ ] **Step 5: 验证几何（列宽合计）**

在脚本里临时打印（不作为断言）：

```bash
python3 -c "
h = {'title':250,'resolution':90,'frame_rate':75,'bitrate':85,'spec':140,
     'audio_channels':55,'audio_sample_rate':90,'duration':75,'file_size':75,'result':60}
t = sum(h.values()) + (len(h)-1)*10 + 45
print(f'列宽 {sum(h.values())} + 手柄 {(len(h)-1)*10} + 序号 45 = {t}')
print(f'可用 1280-10-16 = 1254, 余量 {1254-t}')
"
```

Expected: `= 1130`，`余量 124`（原来是 1245 / 余量 9）

- [ ] **Step 6: 提交**

```bash
git add video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
refactor(grid): 封装/视频编码/音频编码 合并为「规格」列

12 列横向占 1245px, 可用 1254px, 余量仅 9px —— 任何一列稍微拖宽就
触发横向滚动。这三列合计 235px 但在批量检测场景下几乎恒定。

合为 spec 列显示 封装/视频编码/音频编码 (mp4/h264/aac)。
合并后占 1130px, 余量回到 124px。

不影响结构性代码: _build_header / _create_pool_widget / _compute_total_width
的列几何全部由 len(self.columns) 驱动。

顺带删掉 _create_widgets 里从未被读取的 columns 元组 (死代码)。

规格列不可排序 (三个字段合并后排序语义无意义), 四个可排序列全部保留。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 7: 单位规则统一为「header 带单位，cell 不带」

**Files:**
- Modify: `video_checker.py:258`（`frame_rate` 去 ` fps`）、`video_checker.py:285`（`audio_sample_rate` 去 ` kHz`）、`video_checker.py:1726` 附近（`headers` 的帧率表头）
- Modify: `docs/superpowers/specs/2026-09-16-bug-audit-assertions.py:122`
- Test: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`

**Interfaces:**
- Consumes: 无
- Produces: `VideoInfo.frame_rate` 不再带 `" fps"` 后缀；`VideoInfo.audio_sample_rate` 不再带 `" kHz"` 后缀。排序 key 仍用数值字段 `frame_rate_fps` / `sample_rate_khz`，**不受影响**

- [ ] **Step 1: 写失败的测试**

在断言脚本 `main()` 里，`section("_format_cell — spec 列 (Review Focus #2)")` 之前插入：

```python
    section("单位规则 — cell 不带单位, N/A 不受影响 (Review Focus #3)")
    from video_checker import parse_video_info

    def _fake_fmt(**kw):
        d = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                          "r_frame_rate": "30/1"}], "format": {"format_name": "mp4"}}
        d["streams"][0].update(kw)
        return d

    info = parse_video_info(_fake_fmt(bit_rate='32000000', codec_name='h264'),
                            '/x/a.mp4', '/x', 30000.0, 48.0)
    check("frame_rate 不带 fps 后缀", info.frame_rate == '30.00',
          f"got={info.frame_rate!r}")
    check("无音频流时 audio_sample_rate = N/A", info.audio_sample_rate == 'N/A',
          f"got={info.audio_sample_rate!r}")
    check("排序用的 frame_rate_fps 仍是数值", info.frame_rate_fps == 30.0,
          f"got={info.frame_rate_fps!r}")

    d_audio = _fake_fmt(bit_rate='32000000', codec_name='h264')
    d_audio["streams"].append({"codec_type": "audio", "codec_name": "aac",
                               "channels": 2, "sample_rate": "48000"})
    info2 = parse_video_info(d_audio, '/x/a.mp4', '/x', 30000.0, 48.0)
    check("audio_sample_rate 不带 kHz 后缀", info2.audio_sample_rate == '48.0',
          f"got={info2.audio_sample_rate!r}")
    check("排序用的 sample_rate_khz 仍是数值", info2.sample_rate_khz == 48.0,
          f"got={info2.sample_rate_khz!r}")

    d_bad = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                          "r_frame_rate": "0/0", "codec_name": "h264"}],
             "format": {"format_name": "mp4"}}
    info3 = parse_video_info(d_bad, '/x/a.mp4', '/x', 30000.0, 48.0)
    check("r_frame_rate=0/0 → N/A (不是 'nan')", info3.frame_rate == 'N/A',
          f"got={info3.frame_rate!r}")

    section("headers — 表头带单位")
    check("帧率表头含 (fps)",
          "'frame_rate': ('帧率(fps)'" in app_widgets_body(),
          "headers 里帧率列仍是「帧率」")
    check("采样率表头含 (kHz)",
          "'audio_sample_rate': ('采样率(kHz)'" in app_widgets_body())
    check("码率表头含 (kbps)",
          "'bitrate': ('码率(kbps)'" in app_widgets_body())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — `frame_rate == '30.00 fps'`，与期望 `'30.00'` 不符

- [ ] **Step 3: 实现三处改动**

`video_checker.py:258`：

```python
            frame_rate = f"{fps:.2f}"
```

`video_checker.py:285`：

```python
            audio_sample_rate = f"{sample_rate_hz / 1000:.1f}"
```

`video_checker.py` 的 `headers` 里，Task 6 留下的 `'frame_rate': ('帧率', 75),` 改为：

```python
            'frame_rate': ('帧率(fps)', 75),
```

同时更新 `docs/superpowers/specs/2026-09-16-bug-audit-assertions.py:122`：

```python
    check("正常: frame_rate", info.frame_rate == "30.00")
```

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

Run:
```bash
for f in docs/superpowers/specs/*assertions.py; do python "$f" >/dev/null 2>&1 && echo "OK  $f" || echo "FAIL $f"; done
```
Expected: 7 个脚本全部 OK

- [ ] **Step 5: 提交**

```bash
git add video_checker.py docs/superpowers/specs/2026-09-16-bug-audit-assertions.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
refactor(grid): 单位规则统一为「header 带单位, cell 不带」

改动前三列三套规则:
- 码率: header「码率(kbps)」 cell「32000」      ✓
- 采样率: header「采样率(kHz)」 cell「48.0 kHz」 ✗ 单位重复
- 帧率: header「帧率」 cell「25.00 fps」        (单位在 cell)

采样率每行重复 4 个字符的 " kHz"。统一后帧率表头改为「帧率(fps)」。
「帧率(fps)」2 CJK + 5 ASCII ≈ 63px < 75px 列宽, 不会触发表头截断。

排序 key 用的是数值字段 frame_rate_fps / sample_rate_khz, 不受影响。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 8: 状态栏常驻计数

**Files:**
- Modify: `video_checker.py:1662` 附近（`__init__` 加 `_passed_count`）、`video_checker.py:1746-1749`（状态栏）、`video_checker.py:1936-1939`（`_add_result`）、`video_checker.py:1951-1964`（`_scan_complete`）、`video_checker.py:1966-1975`（`_clear_records`）
- Test: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`

**Interfaces:**
- Consumes: 无
- Produces: `VideoCheckerApp.count_var: tk.StringVar`；`VideoCheckerApp._passed_count: int`；`VideoCheckerApp._refresh_counts() -> None`

- [ ] **Step 1: 写失败的测试**

在断言脚本 `main()` 末尾（`root.destroy()` 之前）插入：

```python
    section("状态栏常驻计数 (Review Focus #4)")
    from tkinterdnd2 import TkinterDnD
    from video_checker import VideoCheckerApp
    # 必须用 TkinterDnD.Tk(): VideoGridView.bind_drop 会调 drop_target_register,
    # 普通 tk.Tk() 上会 AttributeError。
    app_root = TkinterDnD.Tk()
    app_root.withdraw()
    app = VideoCheckerApp(app_root)
    app_root.update_idletasks()
    check("初始计数全 0",
          app.count_var.get() == '共 0 · 达标 0 · 不达标 0',
          f"got={app.count_var.get()!r}")

    app._add_result(make_info(title='p.mp4', is_passing=True))
    app._add_result(make_info(title='f.mp4', is_passing=False))
    app._add_result(make_info(title='p2.mp4', is_passing=True))
    app_root.update_idletasks()
    check("插入 3 条后计数正确",
          app.count_var.get() == '共 3 · 达标 2 · 不达标 1',
          f"got={app.count_var.get()!r}")

    app._clear_records()
    app_root.update_idletasks()
    check("清除记录后归零",
          app.count_var.get() == '共 0 · 达标 0 · 不达标 0',
          f"got={app.count_var.get()!r}")
    check("状态文案为「已清除记录」", app.status_var.get() == '已清除记录',
          f"got={app.status_var.get()!r}")

    section("状态栏 — 0 文件完成时计数归零 (Review Focus #4)")
    app.total_files = 0
    app._scan_complete()
    app_root.update_idletasks()
    check("0 文件后计数不残留",
          app.count_var.get() == '共 0 · 达标 0 · 不达标 0',
          f"got={app.count_var.get()!r}")
    check("完成文案简化成「检测完成」",
          app.status_var.get() == '检测完成', f"got={app.status_var.get()!r}")
    app_root.destroy()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — `AttributeError: 'VideoCheckerApp' object has no attribute 'count_var'`

- [ ] **Step 3: 实现**

`__init__`（`:1662` 附近），在 `self._selected_files: list[str] | None = None` 之后加：

```python
        # 达标数增量维护 (避免每次插入都全量 sum, O(N²) in N=1000 时无谓)
        self._passed_count: int = 0
```

状态栏（`:1746-1749`）整段替换为：

```python
        # === 状态栏: 左侧操作状态 + 右侧常驻计数 ===
        self.status_var = tk.StringVar(value="就绪")
        self.count_var = tk.StringVar(value="共 0 · 达标 0 · 不达标 0")
        status_frame = ttk.Frame(self.root, relief='sunken', padding=3)
        status_frame.pack(fill='x', padx=5)
        ttk.Label(status_frame, textvariable=self.count_var, anchor='e').pack(side='right')
        ttk.Label(status_frame, textvariable=self.status_var, anchor='w').pack(
            side='left', fill='x', expand=True)
```

`_add_result`（`:1936-1939`）整段替换为：

```python
    def _add_result(self, info: VideoInfo):
        """添加一条结果到表格"""
        self.video_results.append(info)
        if info.is_passing:
            self._passed_count += 1
        self.grid.add_row(info)
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        """状态栏右侧常驻计数: 共 N · 达标 A · 不达标 B。"""
        total = len(self.video_results)
        passed = self._passed_count
        self.count_var.set(f"共 {total} · 达标 {passed} · 不达标 {total - passed}")
```

`_scan_complete`（`:1951-1964`）：在 `passed = sum(...)` 之后、`self.status_var.set(...)` 之前，把 `self._passed_count = passed` 与 `self._refresh_counts()` 加上，并把非取消分支的文案改成 `"检测完成"`：

```python
        completed = len(self.video_results)
        passed = sum(1 for v in self.video_results if v.is_passing)
        failed = completed - passed
        self._passed_count = passed
        self._refresh_counts()
        if cancelled:
            queued = self.total_files
            pending = max(queued - completed, 0)
            self.status_var.set(
                f"已停止检测：共 {queued} 个视频文件，"
                f"已完成 {completed} 个文件，{pending} 个未检测"
            )
        else:
            self.status_var.set("检测完成")
```

（取消分支原本就带 `pending`，不是纯重复，保留；只是删掉了重复的 `{passed} 个达标，{failed} 个不达标`。若 `failed` 因此不再被使用，一并删掉该变量。）

`_clear_records`（`:1971` 附近），在 `self.video_results.clear()` 之后加：

```python
        self._passed_count = 0
        self._refresh_counts()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

- [ ] **Step 5: 提交**

```bash
git add video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
feat(app): 状态栏右侧常驻计数

「共 200 · 达标 180 · 不达标 20」原本只在扫描完成时出现在状态栏一行,
用户往下滚一屏就看不见了, 且无其他位置保留这些数字。

状态栏拆成左右两段: 左侧操作状态, 右侧常驻计数。完成文案简化为
「检测完成」, 计数信息移交右侧。

达标数增量维护而非每次全量 sum —— 每次插入都 sum 的话 N=1000 时是
O(N²)。

移动文件的两个按钮不需要刷新计数: 它们只移动磁盘文件, 不改
video_results, 网格行数不变。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 9: 截断文本悬停 tooltip

**Files:**
- Create: `video_checker.py` 中新增模块级 `Tooltip` 类（插在 `class EnlargeDialog` 之前，约 `:1085`）
- Modify: `video_checker.py:465` 附近（`VideoGridView.__init__` 加 `_tooltip`）、`video_checker.py:604-646`（`_create_pool_widget` 记 `_full_text`/`_truncated` 并绑 `<Motion>`）、`video_checker.py:961-970`（`_render_visible` 更新这两个属性）、`video_checker.py:1341-1352` 附近（弹窗标题 label 绑 `<Motion>`）
- Test: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`

**Interfaces:**
- Consumes: `format_title` / `truncate_text`（Task 1）；弹窗标题 label 的 `_full_text` / `_truncated`（Task 4）
- Produces: 模块级 `class Tooltip`，构造 `Tooltip(owner: tk.Misc)`，方法 `on_motion(event) -> None` / `hide() -> None`。`VideoGridView.tooltip` 与 `SnapshotProgressDialog.tooltip` 各持一个实例

- [ ] **Step 1: 写失败的测试**

在断言脚本 `main()` 末尾（`root.destroy()` 之前，`VideoCheckerApp` 那段之后）插入：

```python
    section("Tooltip — 只在截断时显示")
    from video_checker import Tooltip

    class _FakeCell:
        def __init__(self, full, truncated):
            self._full_text = full
            self._truncated = truncated

    class _FakeEv:
        def __init__(self, widget):
            self.widget = widget

    tip = Tooltip(root)
    plain = _FakeCell('./a.mp4', False)
    tip.on_motion(_FakeEv(plain))
    root.update_idletasks()
    check("未截断的 cell 不弹 tooltip", tip.win is None, f"got={tip.win!r}")

    cut = _FakeCell('./很长的路径/子目录/文件.mp4', True)
    tip.on_motion(_FakeEv(cut))
    root.update_idletasks()
    check("截断的 cell 弹 tooltip", tip.win is not None)
    if tip.win is not None:
        texts = [w.cget('text') for w in tip.win.winfo_children()
                 if isinstance(w, tk.Label)]
        check("tooltip 显示完整值", texts and texts[0] == cut._full_text,
              f"got={texts}")
        check("tooltip 覆写重定向 (不抢焦点)", bool(tip.win.overrideredirect()))

    tip.on_motion(_FakeEv(cut))     # 同一 cell 再来一次
    root.update_idletasks()
    first_win = tip.win
    tip.on_motion(_FakeEv(cut))
    root.update_idletasks()
    check("同一 cell 重复 hover 不重建窗口", tip.win is first_win)

    tip.on_motion(_FakeEv(plain))   # 移到未截断 cell
    root.update_idletasks()
    check("移到未截断 cell 后关闭", tip.win is None)

    section("网格 cell 带 _truncated 标记")
    g = VideoGridView(ttk.Frame(root), {'title': ('标题', 60)})
    info_cut = make_info(title='x' * 100 + '.mp4', rel_path='./deep/dir')
    info_short = make_info(title='a.mp4', rel_path='./')
    g.add_row(info_cut)      # → pool slot 0
    g.add_row(info_short)    # → pool slot 1
    root.update_idletasks()
    slot0 = g._widget_pool[0]._cells[0][1]    # (key, label) 对, 取 title
    slot1 = g._widget_pool[1]._cells[0][1]
    check("长路径 cell 标了 _truncated", slot0._truncated is True,
          f"text={slot0.cget('text')!r}")
    check("短路径 cell 未标截断", slot1._truncated is False,
          f"text={slot1.cget('text')!r}")
    check("cell 存了 _full_text", slot0._full_text.endswith('.mp4'))
    check("cell 绑了 <Motion>", bool(slot0.bind('<Motion>')))

    section("clear 时关掉悬浮中的 tooltip")
    tip2 = Tooltip(root)
    g.tooltip = tip2
    g.tooltip.show(slot0)
    root.update_idletasks()
    check("tooltip 已弹出", tip2.win is not None)
    g.clear()
    root.update_idletasks()
    check("clear 后 tooltip 已关闭 (不残留旧文本)",
          tip2.win is None, f"got={tip2.win!r}")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: FAIL — `ImportError: cannot import name 'Tooltip' from 'video_checker'`

- [ ] **Step 3: 实现 `Tooltip` 类**

在 `video_checker.py:1083`（`class EnlargeDialog` 之前）插入：

```python
class Tooltip:
    """悬停提示：目标 widget 声明了 _truncated=True 时显示它的 _full_text。

    用法: bind('<Motion>', tooltip.on_motion) 到各个 cell。
    判定截断与取完整文本都由 cell 上的 _truncated / _full_text 负责,
    Tooltip 不关心文本是怎么算出来的。
    """

    def __init__(self, owner: tk.Misc):
        self._owner = owner
        self.win: tk.Toplevel | None = None
        self._cell = None

    def on_motion(self, event) -> None:
        cell = event.widget
        if not getattr(cell, '_truncated', False):
            self.hide()
            return
        if self.win is not None and self._cell is cell:
            return   # 同一 cell 内的移动不重建
        self.show(cell)

    def show(self, cell) -> None:
        self.hide()
        win = tk.Toplevel(self._owner)
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        tk.Label(
            win, text=cell._full_text, font=('TkDefaultFont', 9),
            bg='#ffffe0', fg='black', relief='solid', bd=1,
            justify='left', padx=4, pady=2,
        ).pack()
        win.geometry(f"+{win.winfo_pointerx() + 12}+{win.winfo_pointery() + 18}")
        self.win = win
        self._cell = cell

    def hide(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None
        self._cell = None
```

- [ ] **Step 4: 网格侧接线**

`VideoGridView.__init__`（`:471-472` 的 `_cell_font` 附近）加一行：

```python
        self.tooltip = Tooltip(self.parent)
```

`_create_pool_widget`（`:625-643` 的 cell 创建循环）里，`cell.grid(...)` 之后加：

```python
            # tooltip 用: 记录完整值与是否被截断, 鼠标移动时按需弹窗
            cell._full_text = ''
            cell._truncated = False
            cell.bind('<Motion>', self.tooltip.on_motion)
```

`_render_visible`（`:961-970`）的 cell 写入循环，改为：

```python
            for key, cell in widget._cells:
                full_text = self._format_cell(info, key)
                width = self._col_widths[key]
                new_text = self._truncate_text(full_text, width)
                cell._full_text = full_text
                cell._truncated = (new_text != full_text)
                if cell.cget('text') != new_text:
                    cell.config(text=new_text)
                fg = _cell_fg(info, key)
                new_fg = fg if fg is not None else 'black'
                if cell.cget('fg') != new_fg:
                    cell.config(fg=new_fg)
```

- [ ] **Step 5: 弹窗侧接线**

`SnapshotProgressDialog.__init__` 的 `self._cell_font = tkfont.Font(...)` 之后加：

```python
        self.tooltip = Tooltip(self.top)
```

`_make_row_labels` 里 `title_lbl._truncated = (shown_title != full_title)` 之后加：

```python
        title_lbl.bind('<Motion>', self.tooltip.on_motion)
```

`VideoGridView.clear()`（`:990-996`）在清数据之前加一行 —— 否则用户开着 tooltip
点「清除记录」，浮窗会带着旧文本留在空列表上：

```python
    def clear(self):
        """清空所有数据行，滚动条复位。"""
        self.tooltip.hide()   # 否则 tooltip 会带着旧文本留在空列表上
        self._data.clear()
        ...
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python docs/superpowers/specs/2026-10-23-ui-density-assertions.py`
Expected: PASS，0 failures

- [ ] **Step 7: 跑全量回归 + 提交**

```bash
for f in docs/superpowers/specs/*assertions.py; do python "$f" >/dev/null 2>&1 && echo "OK  $f" || echo "FAIL $f"; done
git add video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -F - <<'EOF'
feat(ui): 截断文本悬停显示完整值

标题列 250px, 长路径被截成 "abc...xyz" 后无任何方式查看完整内容
(只能横向拖宽那一列)。弹窗标题补了路径后同样被截断。

新增模块级 Tooltip 类, 主网格与截图弹窗共用。绑定用 <Motion> 打在
建池时的每个 cell 上 (池 40 x 10 列 = 400 个, 一次性), _render_visible
只改 _full_text / _truncated 属性不重绑。

只在确实被截断时弹窗 —— 判定放在 cell 的 _truncated 上, Tooltip 不
关心文本怎么算出来的。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## 收尾

全部 9 个任务完成后：

```bash
for f in docs/superpowers/specs/*assertions.py; do python "$f" >/dev/null 2>&1 && echo "OK  $f" || echo "FAIL $f"; done
git log --oneline -10
git diff --stat HEAD~9
```

Expected: 7 个断言脚本全部 OK；`video_checker.py` 累计改动应在 150 行量级
（若上千，说明中途某次编辑把 CRLF 转成了 LF）。

手动验收（Windows 上跑 `python video_checker.py`）：

1. 主界面表格 10 列，默认窗口宽度下横向滚动条不出现
2. 拖入跨盘符文件，标题不出现路径重复
3. 截图弹窗加 4 个时间点，底部有横向滚动条且能拖到最右
4. 截图弹窗标题显示 `./A/B/name.mp4`，长路径不撑宽整列
5. 扫描 200 个文件时状态栏右侧计数逐个跳，滚到底部仍可见
6. 主网格与弹窗中长路径悬停出现 tooltip