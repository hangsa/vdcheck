# 用 PySide6 / QTableView 重写 GUI 层 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把主界面的 Tkinter GUI 层换成 PySide6 / `QTableView`，保留全部用户可见行为，并根治「最小化恢复时表格区黑一片」。

**Architecture:** 纯逻辑（~420 行）抽进 `video_core.py` 原样保留；GUI 全部重写为 Qt。表格用 `QAbstractTableModel` + `QTableView`（着色走 `Qt.ForegroundRole`、tooltip 走 `Qt.ToolTipRole`、虚拟滚动/列宽拖拽/排序箭头全部用 Qt 原生能力）。Tk 版在重构期间保留在 `video_checker_tk.py` 里随时可跑可对照，最后删除。

**Tech Stack:** Python 3.10+ / PySide6 ≥ 6.7（实测 6.12）/ PyInstaller。第三方依赖从 `tkinterdnd2` 换成 `PySide6`。

**Spec:** `docs/superpowers/specs/2026-10-27-pyside6-qtableview-design.md` —— 本计划是它的实现论证，spec 是约束权威，两者冲突时以 spec 为准。
**产品文档:** `docs/产品文档.md` —— 框架无关的行为规格，**重写时的验收基准**。

---

## Global Constraints

- **行为零退化。** `docs/产品文档.md` §6 的 **39 条「刻意保留」**一条都不能动。凡是「看着像 bug 但改掉就是行为变更」的地方，重写后必须一模一样。
- **文案逐字不变。** 状态栏 / 弹窗 / 确认框 / 结果框的文案见产品文档 §4.3，包括全角冒号 `：`、间隔号 `·`（U+00B7）、以及「正在检测中，请等待完成后再清除。」（全角逗号）vs「正在检测中, 请等待完成后再截图。」（半角逗号）这种**故意的不一致**。
- **`_cell_fg` 的着色规则一个字不改**，只是 `VideoGridView.FG_PASS` / `FG_FAIL` 两个常量提到 `video_core.py` 的模块级。
- **字体必须显式指定**（`('TkDefaultFont', 9)` 的对应物），不能跟 Qt 的 app 默认字体（macOS 上 13pt），否则所有列的视觉宽度全变。Windows 用 `QFont('Segoe UI', 9)`，macOS 用系统 UI 字体 `setPointSize(9)`。
- **`truncate_text` 的口径不变**：`max_width = col_width - 10`、省略号是三个 ASCII 点 `...`、快路径估算 ASCII 7px/字符 + CJK 14px/字符。字体度量抽象成 `measure: Callable[[str], int]`。
- **只有 `title` 列左对齐，其余全部居中。**（早期设计稿写的是右对齐，以现码为准。）
- **排序箭头是表头文字后缀**（`码率(kbps) ▲`），**不用** `QHeaderView.setSortIndicator`；加完箭头再按列宽截断。
- **两处着色刻意不同**：主表格只红失败列；定点截图弹窗的**标题格整格红字**。
- **目录按需创建**：扫描/浏览阶段绝不建 `snapshot` / `checked`；点「开始截图」/「移动」才建。
- **移动顺序 = 插入序**（不是排序序）：app 侧保留一份插入序的列表专供移动用。
- **只有 `<Control-o>` 一个快捷键。**
- **`video_checker.py` 是 CRLF。** 绝不能用 Python `open().read()`/`write()` 改它（整篇转 LF 会产生约 2000 行幽灵 diff）。只用 Edit 工具做精确替换。**新建的 `video_core.py` / `video_qt.py` 用 LF**（Python 源文件惯例，且断言脚本已是 LF）。
- **没有 pytest。** 断言脚本 `docs/superpowers/specs/<date>-<topic>-assertions.py`，LF，退出码 0 = 通过。
- **打包入口不变**：`build.bat` 与 `video_checker.spec` 的入口始终是 `video_checker.py`。
- **本分支不改 `main`**：`main` 保持 Tk 版可用，直到本分支合并。

## Review Focus

以下五类输入 spec 提到了但没有任务的测试覆盖，各自在下面任务里补测试：

1. **列被拖到最小宽度 20px** —— 表头文字被截成 `...`、箭头可能被截掉、单元格文字全变 `...`，且**表头与正文的列位置仍要一致**。Qt 的 `sectionResized` 会自激（程序设宽度也触发信号），必须用标志位区分。
2. **排序后「序号」列** —— 序号要显示**排序后的新位置**（1-based），不是原始扫描顺序的序号。
3. **排序时滚动位置** —— 现状是原地重绘、滚动位置不变；Qt 的 `beginResetModel` 会跳回顶部，必须保存并恢复。
4. **不达标行的着色矩阵** —— 码率不达标 / 采样率不达标 / 两者都不达标 / 达标，四种组合下**每一格**的颜色都要对；且**序号列永远黑色**。
5. **空表 / 单行 / 超多行** —— 空表时表格不崩、滚动条禁用；超多行时只渲染可见行（虚拟滚动）。

---

## 文件结构

| 文件 | 责任 | 状态 |
|---|---|---|
| `video_core.py` | 纯逻辑：ffprobe 调用、解析、扫描、格式化、着色规则、排序辅助 | **新建**（P1） |
| `video_checker_tk.py` | 现行 Tkinter GUI（重构期间的对照与回归基线） | **新建**（P2 移入）→ P9 删除 |
| `video_qt.py` | 新 Qt GUI：表格模型 + 主窗口 + 定点截图弹窗 + 放大窗口 | **新建**（P2 起） |
| `video_checker.py` | 入口：重新导出 `video_core` 的名字（供断言脚本）+ 调 `video_qt.main()` | P1 改（重新导出）→ P9 换成 Qt 入口 |
| `docs/产品文档.md` | 框架无关的行为规格 | 已完成 |
| `docs/superpowers/specs/2026-10-27-pyside6-qtableview-design.md` | 重构设计 | 已完成 |

**为什么要有 `video_checker_tk.py`**：Tk 版在重构全程保持可跑可对照，每一步都有「旧实现 vs 新实现」的对照物；P9 一次删掉。它不是长期结构。

---

### Task 1: 抽出 `video_core.py`（纯逻辑，行为零变化）

**Files:**
- Create: `video_core.py`（LF）
- Modify: `video_checker.py`（删掉搬走的定义，改为从 `video_core` 导入并**重新导出**）
- Test: 现有 12 个断言脚本（**一行不改**，必须仍全绿）

**Interfaces:**
- Consumes: 现状 `video_checker.py` 的纯函数
- Produces: `video_core.py`，导出
  `get_ffprobe_path` / `get_ffmpeg_path` / `VideoInfo` / `format_duration` / `format_file_size` /
  `parse_time_token` / `time_token_to_string` / `format_title` / `truncate_text` /
  `run_ffprobe` / `extract_frame` / `parse_video_info` / `_cell_fg` /
  `_extract_sort_key` / `_next_sort_state` / `_header_text_with_arrow` / `scan_video_files` /
  `FG_PASS` / `FG_FAIL`
- `truncate_text` 的新签名：`truncate_text(text: str, col_width: int, measure: Callable[[str], int]) -> str`

- [ ] **Step 1: 记录基线**

```bash
for f in docs/superpowers/specs/*-assertions.py; do python "$f" >/dev/null || echo "FAIL $(basename $f)"; done
python3 -c "d=open('video_checker.py','rb').read(); c=d.count(b'\r\n'); print('bare LF:', d.count(b'\n')-c)"
```

预期：无 FAIL，`bare LF: 0`。**把这行输出记在报告里**（重构完要能对照）。

- [ ] **Step 2: 建 `video_core.py`（LF），按现状逐字搬入**

把下列定义从 `video_checker.py` 原样搬进 `video_core.py`（**逐字**，只做两处下面明确写出的改动）：

`get_ffprobe_path` / `get_ffmpeg_path` / `VideoInfo` / `format_duration` / `format_file_size` /
`parse_time_token` / `time_token_to_string` / `format_title` / `run_ffprobe` /
`extract_frame` / `parse_video_info` / `_cell_fg` / `_extract_sort_key` /
`_next_sort_state` / `_header_text_with_arrow` / `scan_video_files`

文件头部的 `import` 照搬现状用到的那些（`json` / `os` / `subprocess` / `sys` / `shutil`? / `ntpath` / `re` / `from dataclasses import dataclass` / `from functools import lru_cache` / `from typing import Callable`）。**不要** import 任何 tkinter 的东西。

**改动 1 —— 颜色常量提到模块级：**

```python
# 表格着色用的两个颜色。原先挂在 VideoGridView 上, 抽到模块级后
# _cell_fg 就能在 core 里用了 (它只依赖这两个常量)。
FG_PASS = '#228B22'   # 绿字 (达标)
FG_FAIL = '#DC143C'   # 红字 (不达标)
```

`_cell_fg` 里的 `VideoGridView.FG_PASS` → `FG_PASS`、`VideoGridView.FG_FAIL` → `FG_FAIL`（**只改这两处引用**，逻辑一行不动）。

**改动 2 —— `truncate_text` 的字体度量抽象成可调用对象：**

```python
def truncate_text(text: str, col_width: int, measure: Callable[[str], int]) -> str:
    """按像素宽截断文本并加省略号。max_width = col_width - 10 (扣 padx + bd)。

    measure: 文本像素宽度的测量函数。Tk 侧传 tkfont.Font.measure,
    Qt 侧传 QFontMetrics.horizontalAdvance —— 截断口径因此完全一致,
    且这个函数不需要建 GUI 就能测。

    提到模块级是因为主网格和定点截图弹窗都要用同一套截断规则。
    """
    max_width = max(0, col_width - 10)
    has_cjk = any(ord(c) >= 0x80 for c in text)
    per_char = 7 if not has_cjk else 14
    if len(text) * per_char <= max_width:
        return text
    if measure(text) <= max_width:
        return text
    ellipsis = '...'
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid] + ellipsis) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis
```

（`measure` 取代原来的 `font.measure(...)`，其余逻辑与现状逐字一致。）

- [ ] **Step 3: `video_checker.py` 改为从 core 导入并重新导出**

在 `video_checker.py` 里删掉搬走的定义，加：

```python
# 纯逻辑已抽到 video_core.py。这里重新导出, 让既有的断言脚本
# (`from video_checker import X`) 不用改。
from video_core import (  # noqa: F401
    FG_FAIL, FG_PASS, VideoInfo, _cell_fg, _extract_sort_key,
    _header_text_with_arrow, _next_sort_state, extract_frame, format_duration,
    format_file_size, format_title, get_ffmpeg_path, get_ffprobe_path,
    parse_time_token, parse_video_info, run_ffprobe, scan_video_files,
    time_token_to_string, truncate_text,
)
# 注意: **不重导出** `_parse_drop_data` —— 它解析的是 tkinterdnd2 的私有格式
# `{f1} {f2}`, Qt 侧走 `mimeData().urls()`, 该函数与它的断言脚本在 Task 5
# 一并删除。
```

**再把 `video_checker.py` 里所有 `truncate_text(...)` 的调用改成传 `measure`：**

现状调用点（用 grep 找全）：

- `VideoGridView._truncate_text`（方法）→ 改成 `return truncate_text(text, col_width, self._cell_font.measure)`
- `VideoGridView._format_cell` 里若直接调 `truncate_text` → 同样改
- `SnapshotProgressDialog._make_row_labels` 里若直接调 → 同样改

```bash
grep -n "truncate_text(" video_checker.py
```

逐个改，**只改最后一个参数**：`font` → `font.measure`。

- [ ] **Step 4: 跑全部断言（必须一行不改就全绿）**

```bash
for f in docs/superpowers/specs/*-assertions.py; do python "$f" >/dev/null || echo "FAIL $(basename $f)"; done
```

预期：**无 FAIL**。

> `2026-10-23-ui-density-assertions.py` 里调 `truncate_text(t, w, font)` 的地方**会失败** —— 签名变了。
> **本任务允许改它，但只许改调用方式**：`truncate_text(t, w, font)` → `truncate_text(t, w, font.measure)`，**断言本身不动**。
> 同样 `2026-09-16-bug-audit-assertions.py` 里 `gv._truncate_text(short, 200)` 是走 `VideoGridView` 的方法，方法内部已改 → 不用动 ✅。
>
> **准确的表述**：**11 个脚本一行不改** + ui-density 的 `truncate_text` **调用点**换写法（断言本身不动）。

- [ ] **Step 5: 验 CRLF + 提交**

```bash
python3 -c "d=open('video_checker.py','rb').read(); c=d.count(b'\r\n'); print('bare LF:', d.count(b'\n')-c)"
```

预期 `bare LF: 0`（`video_core.py` 是新建的 LF 文件，不查）。

```bash
git add video_core.py video_checker.py docs/superpowers/specs/2026-10-23-ui-density-assertions.py
git commit -m "refactor: 纯逻辑抽到 video_core.py, 行为零变化

把 17 个纯函数 + VideoInfo + 两个颜色常量抽进 video_core.py, video_checker.py
改为导入并重新导出 (既有的断言脚本 from video_checker import X 不用改)。

两处必要的改动:
- FG_PASS/FG_FAIL 从 VideoGridView 提到模块级 —— _cell_fg 依赖它们,
  不提到模块级就搬不走 (逻辑本身一个字没改)
- truncate_text 的字体度量抽象成 measure: Callable[[str], int] ——
  原来的 font: tkfont.Font 让这个纯逻辑函数依赖 tkinter。Tk 侧传
  font.measure, Qt 侧传 QFontMetrics.horizontalAdvance, 截断口径完全一致,
  且这个函数不用建 GUI 就能测

12 个断言脚本全绿 (ui-density 的截断用例只换了调用方式, 断言没动)。"
```

---

### Task 2: 修 3 个既有缺陷

**Files:**
- Modify: `video_core.py`（`run_ffprobe` 的平台守卫）
- Modify: `video_checker.py`（阈值校验 + 「未找到视频文件」）
- Test: 新建 `docs/superpowers/specs/2026-10-27-bugfix-assertions.py`

**Interfaces:**
- Consumes: Task 1 的 `video_core.py`
- Produces:
  - `video_core.run_ffprobe` 在非 Windows 上不再崩
  - 阈值校验拒绝 `inf` / `nan`
  - 目录里没有视频文件时，状态栏显示「未找到视频文件」

- [ ] **Step 1: 写会失败的断言**

新建 `docs/superpowers/specs/2026-10-27-bugfix-assertions.py`（LF）：

```python
"""三个既有缺陷的回归断言。

1. run_ffprobe 在非 Windows 上无条件构造 subprocess.STARTUPINFO ——
   macOS/Linux 上 AttributeError 逃出 except 列表, worker 线程静默死亡,
   UI 永久卡在「检测中」。实际后果: 这个 app 只能在 Windows 上跑。
2. 阈值解析接受 inf / nan (float() 通过且 > 0 成立) → 所有行永远不达标。
3. 「未找到视频文件」的状态文案被紧随其后的完成逻辑立刻覆盖, 用户看不到。

跑法: python docs/superpowers/specs/2026-10-27-bugfix-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

_failures: list[str] = []


def check(name, condition, detail=""):
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title):
    print(f"\n=== {title} ===")


def main():
    section("阈值必须是有限的正数 (真调 app 的校验函数)")
    from video_core import parse_threshold
    for bad in ('inf', '-inf', 'nan', '0', '-1', 'abc', ''):
        check(f"{bad!r} 被拒绝", parse_threshold(bad) is None,
              f"got={parse_threshold(bad)!r}")
    check("正常值被接受", parse_threshold('30000') == 30000.0,
          f"got={parse_threshold('30000')!r}")
    check("带空白的正常值被接受", parse_threshold(' 48 ') == 48.0,
          f"got={parse_threshold(' 48 ')!r}")

    section("run_ffprobe 在非 Windows 上不崩 (真调)")
    import video_core
    try:
        r = video_core.run_ffprobe('/nonexistent/definitely-not-a-video.mp4')
        check("返回 None 而不是抛异常", r is None, f"got={r!r}")
    except Exception as e:
        check("返回 None 而不是抛异常", False,
              f"抛了 {type(e).__name__}: {e}")

    section("目录里没有视频文件时状态栏文案")
    from video_checker import VideoCheckerApp
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
    root.withdraw()
    app = VideoCheckerApp(root)
    root.update_idletasks()
    app.total_files = 0
    app._scan_complete()
    root.update_idletasks()
    check("显示「未找到视频文件」",
          app.status_var.get() == '未找到视频文件',
          f"got={app.status_var.get()!r}")

    root.destroy()
    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 2: 跑断言，确认失败**

```bash
python docs/superpowers/specs/2026-10-27-bugfix-assertions.py
```

预期：至少「平台守卫存在」与「显示『未找到视频文件』」两条 FAIL。

- [ ] **Step 3: 修 `run_ffprobe` 的平台守卫**

`video_core.py` 的 `run_ffprobe` 改成与 `extract_frame` **同一套写法**（`extract_frame` 本来就有守卫）：

```python
def run_ffprobe(file_path: str) -> dict | None:
    """调用 ffprobe 获取视频文件信息"""
    try:
        ffprobe_path = get_ffprobe_path()
        kwargs = {
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'timeout': 30,
        }
        # 隐藏控制台只在 Windows 上有意义; 原来无条件构造 STARTUPINFO,
        # macOS/Linux 上 AttributeError 逃出下面的 except 列表 → worker
        # 线程静默死亡 → UI 永久卡在「检测中」。与 extract_frame 保持一致。
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs['startupinfo'] = startupinfo
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            [ffprobe_path, '-v', 'quiet',
             '-print_format', 'json',
             '-show_format', '-show_streams',
             file_path],
            **kwargs,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.decode('utf-8', errors='replace'))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, AttributeError):
        return None
```

> `except` 里加 `AttributeError` 是双保险（万一还有别处漏了平台判断）。

- [ ] **Step 4: 修阈值校验（抽成可测的纯函数）**

在 `video_core.py` 里加一个纯函数（这样断言不用建 GUI 就能测）：

```python
def parse_threshold(text: str) -> float | None:
    """解析单个阈值。非法返回 None。

    现状是内联在 _start_scan 与 _on_file_drop 里各写一遍, 且只判 `> 0`
    —— float() 接受 inf / nan, 而 inf > 0 成立 → 所有行永远不达标。
    """
    try:
        value = float(text.strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value
```

`video_core.py` 顶部加 `import math`。

**两处调用点**都改成用它（`_start_scan` 与 `_on_file_drop` 各一份）：

```python
        bitrate_std = parse_threshold(self.bitrate_var.get())
        if bitrate_std is None:
            messagebox.showerror("错误", "码率标准必须为正数。")
            return
```

```python
        sample_rate_std = parse_threshold(self.sample_rate_var.get())
        if sample_rate_std is None:
            messagebox.showerror("错误", "采样率标准必须为正数。")
            return
```

> 文案**不变**（「码率标准必须为正数。」/「采样率标准必须为正数。」）——
> `inf` / `nan` 也归到这两条文案里，不新增文案。

- [ ] **Step 5: 修「未找到视频文件」被覆盖**

`_scan_complete` 里，把：

```python
        else:
            self._set_status("检测完成", with_counts=True)
```

改成：

```python
        else:
            # 目录里一个视频都没有时, 说「检测完成：共 0 · 达标 0 · 不达标 0」
            # 是废话 —— 统计全是 0, 用户要知道的其实是「没找到文件」。
            # (原来 worker 发过一条「未找到视频文件」, 但紧接着的 done 把它覆盖了,
            #  用户永远看不到。)
            if self.total_files == 0:
                self._set_status("未找到视频文件")
            else:
                self._set_status("检测完成", with_counts=True)
```

> **行为变更**（changelog 记）：空目录时状态栏从「检测完成：共 0 · 达标 0 · 不达标 0」变成「未找到视频文件」。

- [ ] **Step 6: 跑断言 + 全量回归 + 提交**

```bash
python docs/superpowers/specs/2026-10-27-bugfix-assertions.py
for f in docs/superpowers/specs/*-assertions.py; do python "$f" >/dev/null || echo "FAIL $(basename $f)"; done
```

预期：全绿。

```bash
git add video_core.py video_checker.py docs/superpowers/specs/2026-10-27-bugfix-assertions.py
git commit -m "fix: 三个既有缺陷 —— ffprobe 平台守卫 / 阈值 inf-nan / 空目录文案被覆盖

1. run_ffprobe 无条件构造 subprocess.STARTUPINFO → macOS/Linux 上
   AttributeError 逃出 except 列表, worker 线程静默死亡, UI 永久卡在
   「检测中」。改成与 extract_frame 同一套 win32 守卫。**不修就没法在
   macOS 上开发测试**。
2. 阈值解析接受 inf / nan (float() 通过且 > 0 成立) → 所有行永远不达标。
   加 math.isfinite 校验, 文案不变。
3. 「未找到视频文件」的状态文案被紧随其后的完成逻辑立刻覆盖, 用户永远
   看不到。空目录时改显示「未找到视频文件」(不带全是 0 的统计)。

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 3: Qt 表格模型与视图

**Files:**
- Create: `video_qt.py`（LF）
- Test: 新建 `docs/superpowers/specs/2026-10-27-qt-table-assertions.py`（LF）

**Interfaces:**
- Consumes: `video_core` 的 `_cell_fg` / `_extract_sort_key` / `_next_sort_state` / `_header_text_with_arrow` / `truncate_text` / `format_title`
- Produces:
  - `video_qt.ColumnSpec` —— `key: str | None` / `header: str` / `width: int` / `align: str` / `sortable: bool`
  - `video_qt.VIDEO_COLUMNS` —— 11 列的规格（序号 + 10 数据列），照搬产品文档 §4.2
  - `video_qt.VideoTableModel(QAbstractTableModel)` ——
    `rowCount` / `columnCount` / `data` / `headerData` /
    `append_result(info)` / `clear()` / `set_column_width(section, width)` /
    `on_header_clicked(section)` / `sorted_rows` 属性
  - `video_qt.TruncatingDelegate(QStyledItemDelegate)` —— 预先截断的文本 + `Qt.ElideNone`

- [ ] **Step 1: 写会失败的断言**

新建 `docs/superpowers/specs/2026-10-27-qt-table-assertions.py`（LF），核心断言：

```python
"""Qt 表格模型的行为断言。

对照 docs/产品文档.md 的表格规格 (§4.2) 与着色规则 (§3.3)。
跑法: python docs/superpowers/specs/2026-10-27-qt-table-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

_failures = []


def check(name, condition, detail=""):
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title):
    print(f"\n=== {title} ===")


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    from video_qt import VIDEO_COLUMNS, VideoTableModel
    from video_core import VideoInfo, _cell_fg

    def mk(title='a.mp4', passing=True, low_br=False, low_sr=False, bitrate=None):
        if bitrate is None:
            bitrate = 1000.0 if low_br else 32000.0
        return VideoInfo(
            rel_path='./', title=title, resolution='1920x1080',
            frame_rate='30.00', bitrate_kbps=bitrate,
            video_codec='h264', audio_codec='aac', audio_channels='2',
            audio_sample_rate='48.0', duration='00:01:00', file_size='1.0 GB',
            is_passing=passing, sample_rate_passing=not low_sr,
            bitrate_std=30000.0, sample_rate_std=48.0,
            full_path=f'/x/{title}', container_format='mp4',
            frame_rate_fps=30.0, sample_rate_khz=48.0)

    section("列集合")
    check("11 列 (序号 + 10 数据列)", len(VIDEO_COLUMNS) == 11,
          f"got={len(VIDEO_COLUMNS)}")
    check("第 0 列是序号列 (key=None)", VIDEO_COLUMNS[0].key is None)
    check("序号列宽 45", VIDEO_COLUMNS[0].width == 45)
    check("序号列不可排序", not VIDEO_COLUMNS[0].sortable)
    keys = [c.key for c in VIDEO_COLUMNS[1:]]
    check("数据列 key 与产品文档一致",
          keys == ['title', 'resolution', 'frame_rate', 'bitrate', 'spec',
                   'audio_channels', 'audio_sample_rate', 'duration',
                   'file_size', 'result'], f"got={keys}")
    check("只有 title 左对齐, 其余居中",
          [c.align for c in VIDEO_COLUMNS[1:]]
          == ['w'] + ['center'] * 9,
          f"got={[c.align for c in VIDEO_COLUMNS[1:]]}")
    check("可排序列 4 个",
          sorted(c.key for c in VIDEO_COLUMNS if c.sortable)
          == ['audio_sample_rate', 'bitrate', 'frame_rate', 'result'],
          f"got={sorted(c.key for c in VIDEO_COLUMNS if c.sortable)}")

    section("着色矩阵")
    m = VideoTableModel(VIDEO_COLUMNS)
    m.append_result(mk('ok.mp4', passing=True))
    m.append_result(mk('bad.mp4', passing=False, low_br=True, low_sr=True))
    m.append_result(mk('half.mp4', passing=False, low_br=True, low_sr=False))
    app.processEvents()

    def fg_at(row, col):
        idx = m.index(row, col)
        brush = m.data(idx, Qt.ForegroundRole)
        return brush.color().name() if brush else None

    check("达标行全绿",
          all(fg_at(0, c) == '#228b22' for c in range(1, 11)),
          f"got={[fg_at(0, c) for c in range(1, 11)]}")
    red_cols = {c: fg_at(1, c) for c in range(1, 11)}
    check("不达标行只有 title/result/bitrate/audio_sample_rate 红",
          [k for k, v in red_cols.items() if v == '#dc143c']
          == [1, 4, 7, 10],
          f"got={red_cols}")
    check("序号列永远黑色", fg_at(1, 0) == '#000000', f"got={fg_at(1, 0)}")
    check("只码率不达标 → title/result/bitrate 红",
          [c for c in range(1, 11) if fg_at(2, c) == '#dc143c'] == [1, 4, 10],
          f"got={[c for c in range(1, 11) if fg_at(2, c) == '#dc143c']}")

    section("序号列 = 排序后的新位置")
    m2 = VideoTableModel(VIDEO_COLUMNS)
    for i, br in enumerate([50000, 40000, 30000, 20000, 10000]):
        m2.append_result(mk(f'v{i}.mp4', bitrate=br))
    app.processEvents()
    check("初始按插入序",
          [m2.data(m2.index(r, 1), Qt.DisplayRole) for r in range(5)]
          == ['./v0.mp4', './v1.mp4', './v2.mp4', './v3.mp4', './v4.mp4'],
          f"got={[m2.data(m2.index(r, 1), Qt.DisplayRole) for r in range(5)]}")
    m2.on_header_clicked(4)          # bitrate 列 (索引 4) 升序
    app.processEvents()
    check("升序后按码率重排",
          [m2.data(m2.index(r, 1), Qt.DisplayRole) for r in range(5)]
          == ['./v4.mp4', './v3.mp4', './v2.mp4', './v1.mp4', './v0.mp4'],
          f"got={[m2.data(m2.index(r, 1), Qt.DisplayRole) for r in range(5)]}")
    check("序号列跟着新位置 (1..5)",
          [m2.data(m2.index(r, 0), Qt.DisplayRole) for r in range(5)]
          == ['1', '2', '3', '4', '5'],
          f"got={[m2.data(m2.index(r, 0), Qt.DisplayRole) for r in range(5)]}")

    section("排序三态")
    m3 = VideoTableModel(VIDEO_COLUMNS)
    for i in range(5):
        m3.append_result(mk(f'v{i}.mp4'))
    m3.on_header_clicked(4)          # bitrate
    check("第一次点击 → 升序", m3.sort_key == 'bitrate' and not m3.sort_desc,
          f"got={m3.sort_key} {m3.sort_desc}")
    m3.on_header_clicked(4)
    check("第二次点击 → 降序", m3.sort_key == 'bitrate' and m3.sort_desc,
          f"got={m3.sort_key} {m3.sort_desc}")
    m3.on_header_clicked(4)
    check("第三次点击 → 取消排序", m3.sort_key is None,
          f"got={m3.sort_key}")
    m3.on_header_clicked(3)          # 切到 frame_rate (索引 3)
    check("切到别的列 → 重置为升序", m3.sort_key == 'frame_rate' and not m3.sort_desc,
          f"got={m3.sort_key} {m3.sort_desc}")

    section("不可排序列点了没反应")
    m4 = VideoTableModel(VIDEO_COLUMNS)
    m4.append_result(mk('x.mp4'))
    m4.on_header_clicked(1)          # title
    check("title 列点击后无排序态", m4.sort_key is None, f"got={m4.sort_key}")
    m4.on_header_clicked(0)          # 序号
    check("序号列点击后无排序态", m4.sort_key is None, f"got={m4.sort_key}")

    section("排序箭头是表头文字后缀")
    m5 = VideoTableModel(VIDEO_COLUMNS)
    m5.on_header_clicked(4)
    txt = m5.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    check("升序箭头是 ▲ 后缀", txt.endswith('▲'), f"got={txt!r}")
    m5.on_header_clicked(4)
    txt2 = m5.headerData(4, Qt.Horizontal, Qt.DisplayRole)
    check("降序箭头是 ▼ 后缀", txt2.endswith('▼'), f"got={txt2!r}")
    check("非活动列无箭头",
          not m5.headerData(1, Qt.Horizontal, Qt.DisplayRole).endswith(('▲', '▼')),
          f"got={m5.headerData(1, Qt.Horizontal, Qt.DisplayRole)!r}")

    section("tooltip: 只在被截断时给完整值")
    m6 = VideoTableModel(VIDEO_COLUMNS)
    m6.append_result(mk('x' * 200 + '.mp4'))
    app.processEvents()
    tip = m6.data(m6.index(0, 1), Qt.ToolTipRole)
    check("截断的列给完整 tooltip", tip == './' + 'x' * 200 + '.mp4',
          f"got={tip!r}")
    tip2 = m6.data(m6.index(0, 2), Qt.ToolTipRole)
    check("没截断的列不给 tooltip", tip2 is None, f"got={tip2!r}")

    section("空表 / 清空")
    m7 = VideoTableModel(VIDEO_COLUMNS)
    check("空表 rowCount=0", m7.rowCount() == 0)
    m7.append_result(mk('a.mp4'))
    check("加一行后 rowCount=1", m7.rowCount() == 1)
    m7.clear()
    check("clear 后 rowCount=0", m7.rowCount() == 0)

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 2: 跑断言，确认失败**

```bash
python docs/superpowers/specs/2026-10-27-qt-table-assertions.py
```

预期：FAIL（`video_qt` 还不存在 → ImportError）。

- [ ] **Step 3: 写 `video_qt.py` 的列规格与模型**

新建 `video_qt.py`（LF）。先放列规格 + 模型：

```python
"""PySide6 版的界面层。

表格用 QAbstractTableModel + QTableView: 着色走 Qt.ForegroundRole,
tooltip 走 Qt.ToolTipRole, 虚拟滚动/列宽拖拽/横向滚动全部用 Qt 原生能力,
表格区**一个原生子窗口都没有** —— 这是「最小化恢复时表格区黑一片」的根治点
(Qt 顶层窗口是离屏渲染 + 一次性 blit, 不存在画了一半的中间态)。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics

from video_core import (
    FG_FAIL, FG_PASS, VideoInfo, _cell_fg, _extract_sort_key,
    _header_text_with_arrow, _next_sort_state, format_title, truncate_text,
)

# 与现状一致的字体: 9pt。**不能**跟 Qt 的 app 默认字体 (macOS 上 13pt),
# 否则所有列的视觉宽度全变、250px 的含义也变。
UI_FONT = QFont('Segoe UI', 9) if sys.platform == 'win32' else QFont()
if sys.platform != 'win32':
    UI_FONT.setPointSize(9)


@dataclass(frozen=True)
class ColumnSpec:
    """一列的规格。key=None 表示序号列。"""
    key: str | None
    header: str
    width: int
    align: str = 'center'      # 'w' | 'center'
    sortable: bool = False


VIDEO_COLUMNS: list[ColumnSpec] = [
    ColumnSpec(None, '序号', 45, 'center', False),
    ColumnSpec('title', '标题', 250, 'w', False),
    ColumnSpec('resolution', '分辨率', 90),
    ColumnSpec('frame_rate', '帧率(fps)', 75, 'center', True),
    ColumnSpec('bitrate', '码率(kbps)', 85, 'center', True),
    ColumnSpec('spec', '规格', 140),
    ColumnSpec('audio_channels', '声道数', 55),
    ColumnSpec('audio_sample_rate', '采样率(kHz)', 90, 'center', True),
    ColumnSpec('duration', '时长', 75),
    ColumnSpec('file_size', '文件大小', 75),
    ColumnSpec('result', '结果', 60, 'center', True),
]


class VideoTableModel(QAbstractTableModel):
    """主表格的数据模型。

    颜色规则、排序键、箭头文字全部**复用 video_core 的纯逻辑**,
    这里只负责把它们翻译成 Qt 的 role。
    """

    def __init__(self, columns: list[ColumnSpec], parent=None):
        super().__init__(parent)
        self._columns = columns
        self._rows: list[VideoInfo] = []
        self._widths: dict[int, int] = {i: c.width for i, c in enumerate(columns)}
        self.sort_key: str | None = None
        self.sort_desc: bool = False
        self._fm = QFontMetrics(UI_FONT)

    # ---- Qt 必需接口 ----

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        section = index.column()
        col = self._columns[section]
        info = self._rows[index.row()]
        if role == Qt.DisplayRole:
            if col.key is None:                    # 序号列: 排序后的新位置
                return str(index.row() + 1)
            return self._truncated(section, info)
        if role == Qt.ForegroundRole:
            if col.key is None:                    # 序号列永远黑色
                return QBrush(QColor('#000000'))
            return QBrush(QColor(_cell_fg(info, col.key) or '#000000'))
        if role == Qt.ToolTipRole:
            if col.key is None:
                return None
            full = self._full_text(section, info)
            return full if full != self._truncated(section, info) else None
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        if section < 0 or section >= len(self._columns):
            return None
        col = self._columns[section]
        base = col.header
        if col.key is not None and col.sortable and self.sort_key == col.key:
            base = _header_text_with_arrow(base, True, self.sort_desc)
        # 表头文字也按列宽截断 (加完箭头再截, 与现状 _refresh_headers 同序)
        return truncate_text(base, self._widths[section], self._measure)

    # ---- 内部 ----

    def _measure(self, text: str) -> int:
        return self._fm.horizontalAdvance(text)

    def _full_text(self, section: int, info: VideoInfo) -> str:
        key = self._columns[section].key
        if key == 'title':
            return format_title(info)
        if key == 'spec':
            return (f"{info.container_format}/{info.video_codec}"
                    f"/{info.audio_codec}")
        if key == 'result':
            return '达标' if info.is_passing else '不达标'
        return {
            'resolution': info.resolution,
            'frame_rate': info.frame_rate,
            'bitrate': f"{info.bitrate_kbps:.0f}",
            'audio_channels': info.audio_channels,
            'audio_sample_rate': info.audio_sample_rate,
            'duration': info.duration,
            'file_size': info.file_size,
        }[key]

    def _truncated(self, section: int, info: VideoInfo) -> str:
        return truncate_text(self._full_text(section, info),
                             self._widths[section], self._measure)

    def append_result(self, info: VideoInfo) -> None:
        r = len(self._rows)
        self.beginInsertRows(QModelIndex(), r, r)
        self._rows.append(info)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def set_column_width(self, section: int, width: int) -> None:
        """列宽变化 → 重新截断该列的表头文字与单元格文本。"""
        width = max(20, width)                  # 最小 20px, 与现状一致
        if self._widths.get(section) == width:
            return
        self._widths[section] = width
        self.headerDataChanged.emit(QModelIndex(), Qt.Horizontal, section)
        top = self.index(0, section)
        bottom = self.index(max(0, self.rowCount() - 1), section)
        if top.isValid():
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.ToolTipRole])

    def on_header_clicked(self, section: int) -> None:
        """表头点击 → 三态循环 → 就地排序。"""
        col = self._columns[section]
        if col.key is None or not col.sortable:
            return                              # 不可排序列: 完全无反应
        self.sort_key, self.sort_desc = _next_sort_state(
            self.sort_key, self.sort_desc, col.key)
        self._apply_sort()

    def _apply_sort(self) -> None:
        if self.sort_key is None or not self._rows:
            return
        # list.sort 稳定, 同级保留插入序
        self._rows.sort(key=lambda i: _extract_sort_key(i, self.sort_key),
                        reverse=self.sort_desc)
        self.layoutChanged.emit()
```

- [ ] **Step 4: 跑断言，确认通过**

```bash
python docs/superpowers/specs/2026-10-27-qt-table-assertions.py
```

预期：全绿。若有 FAIL，**修实现，不要改断言**。

- [ ] **Step 5: 补 `TruncatingDelegate` + 视图外壳**

```python
class TruncatingDelegate(QStyledItemDelegate):
    """表格 delegate。

    文本在 model 的 data() 里**已经截断好**, 这里关掉 Qt 自己的 elide
    (默认是单字符 '…', 与现状的三个 ASCII 点 '...' 不一致)。
    """
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.textElideMode = Qt.ElideNone
```

（`from PySide6.QtWidgets import QStyledItemDelegate`）

- [ ] **Step 6: 提交**

```bash
git add video_qt.py docs/superpowers/specs/2026-10-27-qt-table-assertions.py
git commit -m "feat(qt): 表格模型 + 列规格, 着色/排序/截断/tooltip 全部复用纯逻辑

QAbstractTableModel 而不是 QStandardItemModel: 后者每格一个
QStandardItem, 1 万行 × 10 列 = 10 万对象。

- 着色走 Qt.ForegroundRole, 复用 _cell_fg —— 规则一个字没改
- 排序在模型内做 (不用 QSortFilterProxyModel): proxy 会在每次插入时
  立刻排序, 而现状是「扫描中按插入序追加, 扫完统一排一次」
- 排序箭头是表头文字后缀 (保留现状, 不用 setSortIndicator)
- 截断在 data() 里预先做好, delegate 关掉 Qt 的 elide —— Qt 默认是
  单字符 '…', 与现状的三个 ASCII 点 '...' 不一致
- tooltip 走 Qt.ToolTipRole, 只在被截断时给完整值
- 字体显式指定 9pt, 不跟 Qt 的 app 默认字体 (macOS 上 13pt)

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 4: 表格断言改测 Qt 模型

**Files:**
- Modify: `docs/superpowers/specs/2026-10-14-virtual-scroll-assertions.py`（**整体改写**）
- Modify: `docs/superpowers/specs/2026-10-26-header-alignment-assertions.py`（**整体改写**）
- Modify: `docs/superpowers/specs/2026-10-23-ui-density-assertions.py`（**删掉 Tk 表格相关小节**，其余保留）
- Modify: `docs/superpowers/specs/2026-10-27-qt-table-assertions.py`（补齐被移走的语义）
- Delete: `docs/superpowers/specs/2026-10-26-restore-black-assertions.py`

**Interfaces:**
- Consumes: Task 3 的 `video_qt.VideoTableModel` / `VIDEO_COLUMNS` / `UI_FONT`
- Produces: 三个脚本的**表格相关**断言改测 Qt 模型；Tk 版表格不再有断言覆盖

> **本任务不改 `video_checker.py` / `video_qt.py`，Tk GUI 也不移走。**
> 原因：三个「app 层」的脚本还依赖 Tk 的 GUI 类，而它们的 Qt 对应物要等 Task 5
> 的主窗口才有。详见下面的「依赖盘点」。

- [ ] **Step 1: 先把依赖盘点清楚（照做，别猜）**

Tk GUI 移走会打断哪些脚本的 import，**逐个查**：

```bash
grep -n "from video_checker import" -A 12 docs/superpowers/specs/*-assertions.py
```

已知的依赖（**动手前自己再核一遍**）：

| 脚本 | 依赖的 GUI 类 | 何时能改 |
|---|---|---|
| `2026-09-16-bug-audit` | `VideoGridView`（`_truncate_text` 小节）、`VideoCheckerApp`（`_get_unique_dest` 小节） | **Task 5**（`_get_unique_dest` 是 app 层方法） |
| `2026-10-26-path-override` | `VideoCheckerApp` | **Task 5** |
| `2026-10-27-bugfix` | `VideoCheckerApp` | **Task 5** |
| `2026-09-29-dnd-parsing` | `_parse_drop_data`（tkinterdnd2 私有格式 `{f1} {f2}`） | **Task 5**：Qt 侧走 `mimeData().urls()`，该格式不存在 → **函数与脚本一并删**；覆盖换成 Qt 侧「含空格路径不被切错」的断言 |
| `2026-10-23-sync-derived-paths` | `VideoCheckerApp` | **Task 5** |
| `2026-10-26-lazy-dir-creation` | `VideoCheckerApp` | **Task 5** |
| `2026-10-14-virtual-scroll` | `VideoGridView` | **本任务** |
| `2026-10-23-ui-density` | `VideoGridView`、`SnapshotProgressDialog`、`VideoCheckerApp` | 表格部分**本任务**；弹窗部分 **Task 6**；app 部分 **Task 5** |
| `2026-10-26-header-alignment` | `VideoGridView` | **本任务** |

- [ ] **Step 2: 整体改写 `2026-10-14-virtual-scroll-assertions.py`**

它测的是 **Tk 特有的虚拟滚动**（40 个池 widget、spacer、window item 的 y 坐标偏移）。
Qt 的 `QTableView` **原生就是虚拟滚动**（只画可见单元格），所以这个脚本在 Qt 版
的对应物是「能滚、能显示全部行、排序后不乱」。整体改写为测 `VideoTableModel`：

| 原小节 | 改测 |
|---|---|
| pool & data separation | 模型只持一份 `_rows`；`rowCount()` 随追加增长 |
| Render — scroll updates slot bindings | 追加 → `data(index, Qt.DisplayRole)` 立刻是新值 |
| Column resize — throttle & minsize | `set_column_width()` → 表头/单元格重新截断 + `headerDataChanged`/`dataChanged` 发出；最小 20px |
| Column resize — release flushes pending text | 同一套语义（Qt 没有节流） |
| Column resize — unmapped widget skips | **删掉**（Qt 无池 widget） |
| clear() — resets data & bindings | `clear()` → `rowCount()==0`、无残留 |
| reorder() — sorts in place | `reorder()` 按当前排序态重排；无排序态是 no-op |
| Scroll — 无上/下空白 | **删掉**（Qt 不存在 spacer 问题）；换成「滚动不影响行的内容」 |
| Scroll — 未用池 slot 移出布局 | **删掉** |
| Scroll — 无隐藏内容时滚轮/滚动条禁用 | **删掉**（Qt 原生） |
| Public API — preserved | 模型的公开 API：`rowCount`/`columnCount`/`data`/`headerData`/`append_result`/`clear`/`reorder`/`is_sorted`/`set_column_width`/`on_header_clicked` |
| Bound keys — `_on_data_configure` no longer bound | **删掉**（Tk 特有） |

- [ ] **Step 3: 整体改写 `2026-10-26-header-alignment-assertions.py`**

它测的是「表头与正文逐列 Δ=0」+「横滚时表头跟着动」。Qt 的表头与正文**在同一个
`QTableView` 里**，天然对齐 —— 所以改测：

| 原小节 | 改测 |
|---|---|
| 表头与正文 canvas 同宽 | 同一个 view 的表头与 viewport 宽度一致 |
| 表头与正文逐列对齐 (Δ=0) | 表头 section 的 x == 模型 `_col_layout` 的 x（逐列） |
| 横向滚动时表头跟着动 | **删掉**（同一个 view 天然同步）—— 换成「排序后列位置不变」 |
| 拖宽列后表头列宽同步 | `set_column_width()` → 表头文字重新截断 + `headerDataChanged` |
| 表头点击排序仍可用 | `on_header_clicked()` → 排序态翻转 + 行顺序改变 |

- [ ] **Step 4: `2026-10-23-ui-density-assertions.py` 删掉 Tk 表格相关小节**

删掉（其余小节保留，弹窗部分等 Task 6、app 部分等 Task 5）：

- `section("_format_cell — title 分支委托 format_title")`
- `section("单位规则 — cell 不带单位, N/A 不受影响")`
- `section("headers — 表头带单位")`
- `section("_format_cell — spec 列")`
- `section("列集合 — 10 列 + 四个可排序列仍存在")`
- `section("VideoGridView._truncate_text — 委托且行为一致")`
- `section("网格 cell 带 _truncated 标记")`

**注意**：`section("truncate_text — 像素级截断")` **保留**（它测的是 `video_core.truncate_text`，与框架无关）。

被移走的语义**必须在 `2026-10-27-qt-table-assertions.py` 里有对应断言**（补齐）：

| 移走的语义 | 补到 Qt 脚本的断言 |
|---|---|
| title 分支委托 `format_title` | `data(idx, Qt.DisplayRole)` == `format_title(info)` |
| 单位规则（cell 不带单位） | 帧率格 = `'30.00'`（不是 `'30.00 fps'`）、采样率格 = `'48.0'`；`N/A` 字段仍是 `'N/A'` |
| headers 带单位 | 表头文字 == `'帧率(fps)'` / `'码率(kbps)'` / `'采样率(kHz)'` |
| spec 列格式 | `data(idx, Qt.DisplayRole)` == `'mp4/h264/aac'` |
| 10 列 + 4 个可排序列 | 已在 Qt 脚本里（`VIDEO_COLUMNS` 那节）—— 确认覆盖 |

- [ ] **Step 5: 删掉 `2026-10-26-restore-black-assertions.py`**

```bash
git rm docs/superpowers/specs/2026-10-26-restore-black-assertions.py
```

它断言的是 **Tk 的池 widget 映射数**（`len([w for w in grid._widget_pool if w.winfo_manager()]) <= 40`）。
Qt 下**没有对应量**：`QTableView` 有 viewport / 表头 / 滚动条，不是「零子窗口」。

这条断言的**意图**（恢复时不用逐个重绘几百个 HWND）由 Qt 架构保证 —— Qt 顶层窗口是
离屏渲染 + 一次性 blit，且 `QTableView` 只画可见单元格。

**不能改成「断言通过」了事。** 要么删（意图由架构保证），要么换成可测的等价断言。

- [ ] **Step 6: 跑全部断言**

```bash
for f in docs/superpowers/specs/*-assertions.py; do python "$f" >/dev/null || echo "FAIL $(basename $f)"; done
```

预期：全绿（13 个脚本 —— 删掉 restore-black 后是 13；新增的 Qt 表格脚本已在）。

- [ ] **Step 7: 提交**

```bash
git add -A docs/superpowers/specs/
git commit -m "test(qt): 表格断言改测 Qt 模型

Tk 特有的虚拟滚动 (40 个池 widget / spacer / window item 偏移) 在 Qt 里
不存在 —— QTableView 原生就是虚拟滚动 (只画可见单元格)。所以这两个脚本
整体改写为测 VideoTableModel: 追加/清空/排序/列宽变化/公开 API。

表头正文 Δ=0 那个脚本同理改写: Qt 的表头与正文在同一个 QTableView 里,
天然对齐, 改成验「表头 section 的 x == 模型列的 x」。

ui-density 删掉 7 个 Tk 表格小节, 对应语义补进 Qt 表格脚本。

删掉 restore-black 脚本: 它断言 Tk 的池 widget 映射数, Qt 下无对应量
(QTableView 有 viewport/表头/滚动条, 不是零子窗口)。该断言的意图
(恢复时不用逐个重绘几百个 HWND) 由 Qt 架构保证。

Co-Authored-By: Claude <noreply@anthropic.com>"
```


### Task 5: Qt 主窗口

**Files:**
- Modify: `video_qt.py`
- Test: 新建 `docs/superpowers/specs/2026-10-27-qt-main-assertions.py`

**Interfaces:**
- Consumes: `video_core` 的纯函数、Task 3 的 `VideoTableModel`
- Produces: `video_qt.MainWindow(QMainWindow)` —— 布局、状态栏、按钮态、扫描流程、拖拽、派生路径、目录按需创建、移动文件

- [ ] **Step 1: 写会失败的断言**（新建 `2026-10-27-qt-main-assertions.py`）

覆盖：
- 状态栏文案序列：初始「就绪」→ 扫描中「正在检测... (i/N) 文件名」→ 终态「检测完成：共 N · 达标 P · 不达标 X」（**全角冒号 + 间隔号**）
- 停止文案「已停止检测（已完成 N/M）：共 X · 达标 Y · 不达标 Z」（**只一个冒号**）
- 派生路径：`_sync_derived_paths` 后迁移地址 = `<源>\checked`、截图地址 = `<源>\snapshot`，**且不建目录**
- 「浏览」语义：结果是父目录 + 套一层子目录
- 按钮状态机：`开始检测` → `检测中` → `正在停止...`（禁用）→ `开始检测`
- 清除记录：清结果/表格/计数/迁移地址/截图地址，**不清阈值**
- 移动文件：确认框文案、`_1` 去重后缀、失败清单最多 20 条、**移动顺序 = 插入序**
- 拖拽：立即开始扫描、拒绝规则（非视频扩展名 / 扫描中 / 目录）

- [ ] **Step 2: 跑断言，确认失败** → 预期 ImportError / FAIL

- [ ] **Step 3: 写 `MainWindow`（含列宽的两个方向接线）**

> **Task 4 审查带过来的硬要求**：列宽的接线有两个方向，**都要有断言**。
> Task 4 之前的脚本是**自己手动**同时调 `model.set_column_width()` 和
> `view.setColumnWidth()` → 断言的是它自己的接线，真实路径一条没覆盖。
>
> - **model → view**：启动时把 `model._widths` 推给视图。忘了 → 单元格按错的
>   宽度截断、表格错位
> - **view → model**：Qt 里**用户拖表头**直接改表头 section 大小，必须把
>   `sectionResized` 接到 `model.set_column_width`，否则模型 `_widths` 变陈旧、
>   单元格继续按旧宽度截断。**Tk 原脚本测的正是这条路径**
>
> 断言形态（写在 `2026-10-27-qt-main-assertions.py` 里）：
> 1. 视图的 section 宽度 == `model._widths[i]`（model → view 生效）
> 2. 改视图的列宽后，`model._widths[i]` 跟着变（view → model 生效）
>
> 另外：Task 4 的 `header-alignment` 脚本里那个「表头 x == 视图 x」是 Qt 的
> **API 恒等式**（`QTableView::columnViewportPosition()` 就是
> `horizontalHeader()->sectionViewportPosition()`），**永远相等**。真正的对齐
> 不变式是**表头 x == 模型 x**（模型自己的几何），那条才抓得住断线。


按产品文档 §4.1 的布局逐控件建。文案**逐字照搬**（见产品文档 §4.3）。

关键实现点（逐条照产品文档）：

| 需求 | 实现 |
|---|---|
| 状态栏 | `QStatusBar` + `setStyleSheet` 做凹陷边框；**一行文字**；`set_status(text, with_counts=False)` 复刻现状的「终态追加统计」逻辑 |
| 按钮三态 | `开始检测` → `检测中`（**仍可点**，点即取消）→ `正在停止...`（禁用）→ `开始检测` |
| 扫描线程 | `QThread` + `ScanWorker(QObject)`，`result_ready(VideoInfo)` / `progress(str)` / `finished()` / `cancelled()` 四个信号，**queued** 连回主线程 |
| 结果边扫边出 | `result_ready` → 主线程 `model.append_result(info)` → 表格增量刷新 |
| 排序收尾 | 扫描结束时若处于排序态 → 统一排一次（复刻现状 `_scan_complete` 里的 `reorder`） |
| 派生路径 | 复刻 `_sync_derived_paths`：迁移地址**无条件**转 `\`、截图地址**仅 win32** 转、**无条件覆盖**用户值、**只填字段不建目录** |
| 目录按需创建 | 「开始截图」/「移动」时才 `os.makedirs` |
| 拖拽 | `setAcceptDrops(True)` + `dragEnterEvent`（接受含文件的 URL）+ `dropEvent`（`mimeData().urls()` → 本地路径） |

#### Qt 特有的行为对齐清单（Task 3 审查查出来的，漏了就会静默退化）

| # | 事项 | 不做会怎样 |
|---|---|---|
| 1 | **`UI_FONT` 必须在 `QApplication` 存在之后求值** | `QFont()` 会拷贝当前 app 的默认字体；app 还不存在时字体族是空的 → `QFontMetrics(UI_FONT)` **直接 abort 进程**（`QFontDatabase: Must construct a QGuiApplication before accessing QFontDatabase`），字宽也整套偏掉（`码率(kbps)` 48px→67px） |
| 2 | `app.setFont(UI_FONT)` | 模型用 `QFontMetrics(UI_FONT)` 量宽度，视图用 app 字体渲染。两者不一致 → 预截断的文字**溢出列宽**（因为 elide 是关的） |
| 3 | 表头 `setElideMode(Qt.ElideNone)` | 模型已把表头文字截断好，Qt 再 elide 一次 → 双重截断 |
| 4 | 视图 `setShowGrid(False)` | Qt 默认画 1px 网格线；Tk 的 `tk.Label` 是无边框的 → 表格多出一圈网格线 |
| 5 | 序号列 `QHeaderView.Fixed` + 45px；其余 `Interactive` | 序号列可被拖宽 → 与「固定 45px」矛盾 |
| 6 | `setSortingEnabled(False)` | Qt 自带的排序指示箭头会与「表头文字后缀 `▲`」同时出现 |
| 7 | **表头按 section 对齐** —— `QHeaderView` 没有按 section 设对齐的 API | Tk 版：title 列表头**左对齐**、其余居中。Qt 默认全居中 → 表头「标题」跑到 250px 格的中间，而正文 title 列是**左**的 → 两者对不上，很显眼。方案：写个 `QHeaderView` 子类覆写 `paintSection`，title 列左对齐、其余保持居中 |
| 8 | 排序后如果视图有 current/selection，考虑补 `layoutAboutToBeChanged` | 模型只发 `layoutChanged`，视图的 current/selection 索引不跟随数据项重映射。Tk 版无选中态 → **非回归**，但接线时要知道 |

> 第 7 条需要你拍板：**接受表头全居中**（省 15 行自定义绘制，但表头与正文对不齐），
> 还是**写 `QHeaderView` 子类**保真。计划里按保真写（子类），实现时若觉得不值可以改，
> 但要在 changelog 里写明差异。
| 拖入即扫 | drop 处理完**直接开始扫描**，不等用户点「开始检测」 |
| 手改路径即生效 | 路径输入框的 `textChanged` → 清掉「拖拽留下的文件列表」模式（复刻现状 `_on_path_edited`） |
| 移动文件 | 遍历**插入序**的那份列表（不是排序后的显示序）；确认框文案逐字照搬；`_1` 去重；失败清单最多 20 条 |
| 快捷键 | 只绑 `<Control-o>` → 浏览文件夹 |
| 「浏览」 | `QFileDialog.getExistingDirectory`；结果是**父目录**，再套 `checked` / `snapshot` |
| 字体 | 全 app 统一 9pt（`UI_FONT`） |

- [ ] **Step 4: 改写三个 app 层断言脚本（它们一直 import Tk 的 GUI 类）**

Task 4 的依赖盘点里列了三个脚本，它们测的是**app 层行为**，Tk 的 `VideoCheckerApp`
一移走就 import 断了。Qt 主窗口写完后，它们对应的实现是 `video_qt.MainWindow`：

| 脚本 | 原来 import | 改成 | 改测什么 |
|---|---|---|---|
| `2026-10-23-sync-derived-paths-assertions.py` | `VideoCheckerApp` | `video_qt.MainWindow` | 派生路径（迁移地址 = `<源>\checked`、截图地址 = `<源>\snapshot`）、无条件覆盖、只填字段不建目录、浏览选父目录 |
| `2026-10-26-lazy-dir-creation-assertions.py` | `VideoCheckerApp` | `video_qt.MainWindow` | 扫描/浏览阶段不建目录；点「开始截图」才建 snapshot；点「移动」才建 checked |
| `2026-09-16-bug-audit-assertions.py` | `VideoGridView` + `VideoCheckerApp` | `video_qt`（表格部分）+ `video_qt.MainWindow`（`_get_unique_dest` 部分） | `_truncate_text` 那小节改成直接测 `video_core.truncate_text` 的快路径（**不再需要 `VideoGridView.__new__` + `_cell_font = None` 那套**）；`_get_unique_dest` 那小节改测 Qt 版的去重命名函数 |

> **只许改「测什么」和「import 什么」，不许把断言改弱。** 每个改写都要写出
> 「原语义 → 新断言」对照（写进报告）。
> `bug-audit` 的其余小节（scan_video_files / parse_video_info ×3 / _cell_fg 全矩阵 /
> format_duration / format_file_size）**一个字都不许动**。

- [ ] **Step 5: 跑断言，确认通过** → 预期全绿

- [ ] **Step 6: 提交**

```bash
git add video_qt.py docs/superpowers/specs/
git commit -m "feat(qt): 主窗口 —— 布局/状态栏/按钮态/扫描线程/拖拽/迁移

文案逐字照搬 docs/产品文档.md §4.3, 含全角冒号、间隔号, 以及
「正在检测中，请等待完成后再清除。」(全角逗号) vs
「正在检测中, 请等待完成后再截图。」(半角逗号) 这种故意的不一致。

- 扫描用 QThread + 信号, 替代「线程 + queue + 100ms 轮询」;
  结果仍边扫边出
- 派生路径复刻 _sync_derived_paths: 迁移地址无条件转反斜杠、截图地址
  仅 win32 转、无条件覆盖用户值、只填字段不建目录
- 移动遍历**插入序**的那份列表 (不是排序后的显示序) —— 现状如此
- 拖入用 mimeData().urls(), 含空格的路径不再被切错

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 6: Qt 定点截图弹窗 + 放大窗口

**Files:**
- Modify: `video_qt.py`
- Test: 新建 `docs/superpowers/specs/2026-10-27-qt-snapshot-assertions.py`

**Interfaces:**
- Consumes: `video_core` 的 `parse_time_token` / `time_token_to_string` / `format_title` / `extract_frame`
- Produces: `video_qt.SnapshotDialog(QDialog)` / `video_qt.EnlargeDialog(QDialog)` / `video_qt.SnapshotGridModel` / `video_qt.SnapshotDelegate`

- [ ] **Step 1: 写会失败的断言**（新建 `2026-10-27-qt-snapshot-assertions.py`）

覆盖：
- 默认时间点 `00:00:07:24` / `00:00:08:00`；`+` 追加 `00:00:00:00`；**无删除按钮**
- 截图地址默认 `<源>\snapshot`；「浏览」选父目录 → 套一层 `snapshot`；**不建目录**
- 表头文字 `T1: 00-00-07-24`（`time_token_to_string`）
- 初始只有序号 + 标题两列；起跑后按 N 个时间点重建列
- 单元格三态：加载中 `…` 蓝 / 成功画图 / 失败 `×` 红
- **标题格不达标时整格红字**（与主表格「只红失败列」刻意不同）
- 标题格按 250px 截断 + `Qt.ToolTipRole` 给完整路径
- 输出文件名 `{base}_t{c+1}_{00-00-07-24}.png`
- 抽帧中点关闭 → 二次确认「抽帧还在进行中, 确定要关闭吗?」

- [ ] **Step 2: 跑断言，确认失败**

- [ ] **Step 3: 实现**

- 网格：`QTableView` + `SnapshotGridModel` + `SnapshotDelegate`（**委托画 QPixmap**，不用 QLabel → 零子窗口）
- 单元格状态存 model 的 `dict[(row, col)] -> CellState`
- 缩略图：worker 只产出**磁盘路径**，主线程加载 320px `QPixmap` 放 delegate；放大窗口**从磁盘重新加载** 640px 原图（`QImage`）
- 时间点输入：`QLineEdit` 子类重写 `mouseDoubleClickEvent`，按 x 坐标定位段（HH / MM / SS / frame）只选该段
- 格式校验用 `parse_time_token`
- 抽帧用 `extract_frame`（core 里，逻辑不改）

- [ ] **Step 4: 跑断言，确认通过**

- [ ] **Step 5: 提交**

```bash
git add video_qt.py docs/superpowers/specs/2026-10-27-qt-snapshot-assertions.py
git commit -m "feat(qt): 定点截图弹窗 + 放大窗口

网格用 QTableView + 自定义 delegate 画 QPixmap, 不用 QTableWidget
(每格一个 QLabel) —— 表格区不留子窗口。

- 单元格三态: 加载中 … 蓝 / 成功画图 / 失败 × 红
- **标题格不达标时整格红字** —— 与主表格「只红失败列」刻意不同
- 标题格按 250px 截断 + ToolTipRole 给完整路径
- 缩略图 320px; 放大窗口**从磁盘重新加载** 640px 原图 (省内存, 与现状一致)
- 时间点输入: QLineEdit 子类按 x 坐标只选所在段
- 抽帧中点关闭 → 二次确认; 不 kill 已启动的 ffmpeg

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Task 7: 入口切换 + 打包 + 删 Tk 版

**Files:**
- Modify: `video_checker.py`（换成 Qt 入口）
- Delete: `video_checker_tk.py`
- Modify: `video_checker.spec`
- Modify: `build.bat`
- Modify: `CLAUDE.md`
- Test: 全部断言脚本 + Windows 实测

**Interfaces:**
- Consumes: Task 1~6 的全部产出
- Produces: `video_checker.py` = Qt 入口 + 重新导出 core 的名字

- [ ] **Step 1: `video_checker.py` 换成 Qt 入口**

```python
"""视频码率检查器 —— 入口。

界面实现在 video_qt.py (PySide6); 纯逻辑在 video_core.py。
这里重新导出 core 的名字, 让既有的断言脚本
(`from video_checker import X`) 不用改。
"""
import sys

from video_core import (  # noqa: F401
    FG_FAIL, FG_PASS, VideoInfo, _cell_fg, _extract_sort_key,
    _header_text_with_arrow, _next_sort_state, extract_frame, format_duration,
    format_file_size, format_title, get_ffmpeg_path, get_ffprobe_path,
    parse_time_token, parse_video_info, run_ffprobe, scan_video_files,
    time_token_to_string, truncate_text,
)


def main():
    from video_qt import main as qt_main
    qt_main()


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 删掉 Tk GUI**

Task 4 推迟了这一步（因为 app 层的三个断言脚本还 import Tk 的类）。现在 Qt 版
完整了，删：

- `video_checker_tk.py`（整个文件）—— 如果之前没建过就跳过这步
- `video_checker.py` 里的 `VideoGridView` / `Tooltip` / `EnlargeDialog` /
  `SnapshotProgressDialog` / `VideoCheckerApp` 五个类，以及 `tkinter` /
  `tkinterdnd2` / `tkinter.ttk` 的 import

```bash
git rm video_checker_tk.py
```

> **这一步之后**，`video_checker.py` 只剩 core 的重新导出 + 转发到 `video_qt.main()`。
> 剩下的脚本里若还有 `from video_checker import VideoCheckerApp` 之类的 import，
> 在这一步里一并改成 `from video_qt import ...`。

- [ ] **Step 3: 改 `video_checker.spec`**

| 项 | 变更 |
|---|---|
| `datas` | 去掉 `get_tkinterdnd2_data()`，保留 `get_ffprobe_data()` |
| `hiddenimports` | 去掉 `tkinterdnd2` / `tkdnd` |
| `excludes` | 加 `PySide6.QtQuick` / `PySide6.QtWebEngine*` / `PySide6.Qt3D*` / `PySide6.QtMultimedia*` / `PySide6.QtCharts` / `PySide6.QtDataVisualization` / `PySide6.QtPdf` / `PySide6.QtQml` —— **排除前逐个确认没用到**（`QtNetwork` 大概率被 `QtWidgets` 依赖，排掉会构建失败） |
| 其余 | 不变（单文件、UPX、`console=False`、名字 `视频码率检查器`） |

- [ ] **Step 4: 改 `build.bat`**

| 步骤 | 变更 |
|---|---|
| Python 版本检查 | 「3.8+」→ **「3.10+」**，并加**真正的版本比较**（现状只检查「有没有 python」） |
| 依赖安装 | `pip install tkinterdnd2 pyinstaller` → `pip install PySide6 pyinstaller` |
| 其余 | 不变（下载 FFmpeg → 清理 → PyInstaller） |

- [ ] **Step 5: 更新 `CLAUDE.md`**

- 架构段：GUI 从「自绘 Canvas 网格」改成「QAbstractTableModel + QTableView」
- 依赖：`tkinterdnd2` → `PySide6`
- 保留「纯逻辑层」那一段的函数清单
- 保留「列几何由 Python 拥有」的说明（`_layout_columns` 的等价物在 Qt 版是模型的 `_widths`）

- [ ] **Step 6: 跑全部断言**

```bash
for f in docs/superpowers/specs/*-assertions.py; do python "$f" >/dev/null || echo "FAIL $(basename $f)"; done
```

预期：全绿。

- [ ] **Step 7: 提交**

```bash
git add -A
git commit -m "feat!: GUI 层换成 PySide6, 删掉 Tk 版

- video_checker.py 变成 Qt 入口 (打包入口不变), 重新导出 core 的名字
  给断言脚本用
- video_checker_tk.py 删除 (重构期间的对照物, 到此完成使命)
- video_checker.spec: 去掉 tkinterdnd2 的 datas/hiddenimports, 加
  PySide6 用不到的模块的 excludes (逐个确认过没用到)
- build.bat: Python 3.8+ → 3.10+ 并加真正的版本比较; 依赖换 PySide6
- CLAUDE.md 更新

Co-Authored-By: Claude <noreply@anthropic.com>
```

- [ ] **Step 8: Windows 实测（人工）**

1. `build.bat` 跑通，产物 `dist\视频码率检查器.exe`
2. 记录 exe 体积（与 Tk 版对比）
3. **扫 200 个文件 → 最小化 → 从任务栏恢复 → 表格区不出现黑屏**（本次的靶子）
4. 逐条走产品文档 §9 的验收清单
5. 逐条走产品文档 §6 的 39 条「刻意保留」

---

## 收尾（全部任务完成后）

1. 跑一次全量断言，确认全绿
2. `docs/产品文档.md` 的版本号更新（`v3.3` → `v4.0`，因为换了 GUI 层）
3. 在 `黑窗问题.md` 补一段结论：Tk 侧为什么治不了、Qt 侧为什么能治、实测如何
4. 合并回 `main`（`--no-ff`，保留分支历史）
