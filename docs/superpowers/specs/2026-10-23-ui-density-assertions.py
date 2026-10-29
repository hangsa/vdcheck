"""界面信息展示效率优化 一次性断言脚本。

验证：
- format_title 全部 rel_path 形态 (含跨盘符绝对路径 / UNC);
- truncate_text 像素级截断 (ASCII / CJK / 边界);
- 状态栏文案 (一行合并 + 计数归零 + 中途停止);
- 扫第二轮计数不累加 (行为测试)。

**表格相关的小节已迁走** (2026-10-27, Tk → Qt 重构):
`_format_cell` (title/spec)、表头带单位、10 列集合、`_truncate_text` 委托、
网格 cell 的 `_truncated` 标记 —— 这些断言的是 Tk 的 VideoGridView 内部结构,
对应的语义改测 Qt 模型, 见
`docs/superpowers/specs/2026-10-27-qt-table-assertions.py`。

**定点截图弹窗的小节也迁走了** (2026-10-27, 同一个 Tk → Qt 重构):
横向滚动 / 表头与数据列对齐 / 空文件列表 / 标题限宽 / 缩略图只留 320px /
滚轮绑定 —— 前五项对应的语义见
`docs/superpowers/specs/2026-10-27-qt-snapshot-assertions.py`;
**滚轮绑定那节直接删掉, 不补 Qt 版** —— 逐 widget 绑 `<MouseWheel>` 是
Tk 事件派发机制的产物, `QTableView` 原生支持滚轮, 不需要逐 widget 绑。

另外两处**行为差异** (不是 bug, 是构造方式不同带来的):
- 旧 Tk 弹窗把表头做成 grid 里的一个 widget, 纵向滚动时随内容滚走 (不钉住);
  Qt 的表头本来就是独立控件, 天然钉在顶部。
- 旧 Tk 弹窗的 `mark_done` 用 `PhotoImage.subsample(2,2)` 得到 320px 的
  display, 640px 原图出作用域即回收; Qt 版同样只留 320px, 放大窗口从磁盘
  重新加载 640px 原图。

**不再依赖 tkinter** (2026-10-27): 原来这里 `import tkinter` + `tk.Tk()` 只
为了给 `truncate_text` 一个 font-measure 回调。现在整个项目已经是 Qt-only,
`video_core.truncate_text` 的 `measure: Callable[[str], int]` 接缝就是为这个
准备的 —— 换成 `QFontMetrics(ui_font()).horizontalAdvance`, 断言原样成立。

运行: python docs/superpowers/specs/2026-10-23-ui-density-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import atexit
import os
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))


_tmpdirs: list[str] = []


def mkdtemp(prefix: str = 'vdcheck_') -> str:
    """建临时目录, 记下来, 退出时清掉。

    断言脚本建了一堆 vdcheck_* 目录又不删 —— 用户的 $TMPDIR 会被塞满。
    """
    path = tempfile.mkdtemp(prefix=prefix)
    _tmpdirs.append(path)
    return path


def cleanup_tmpdirs() -> None:
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)
    _tmpdirs.clear()


atexit.register(cleanup_tmpdirs)

# 没有显示环境时 Qt 用 offscreen 平台 (会打一条字体族警告到 stderr, 不在范围)。
# 必须在建 QApplication **之前**设好。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from video_checker import VideoInfo, format_title, truncate_text  # noqa: E402

# QApplication 必须**先于** video_qt 建好: ui_font() 惰性求值时会拷贝当前
# app 的默认字体, app 还不存在时字体族是空的 (family=''), 字宽度量整套
# 偏掉, QFontMetrics(那个字体) 直接 abort 进程。
qt_app = QApplication.instance() or QApplication(sys.argv)

import video_qt  # noqa: E402
from video_qt import MainWindow, ui_font  # noqa: E402


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


def fake_ffprobe_json() -> dict:
    """一段合法的 ffprobe JSON (不真的调 ffprobe)。"""
    return {
        "streams": [
            {"codec_type": "video", "codec_name": "h264",
             "width": 1920, "height": 1080, "r_frame_rate": "30/1",
             "bit_rate": "32000000"},
            {"codec_type": "audio", "codec_name": "aac",
             "channels": 2, "sample_rate": "48000"},
        ],
        "format": {"duration": "120.0", "size": "100000000",
                   "format_name": "mp4"},
    }


def wait_scan(win: MainWindow, timeout: float = 15.0) -> bool:
    """等一轮扫描结束 (worker 在另一个线程里跑)。"""
    end = time.time() + timeout
    while win.scanning and time.time() < end:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    return not win.scanning


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

    # 字体度量: **Qt 版** —— QFontMetrics(ui_font()).horizontalAdvance 顶上
    # Tk 版的 tkfont.Font.measure。断言都是相对宽度的, 换掉度量来源后
    # 断言原样成立 (而且这个项目已经不该再需要 tkinter)。
    metrics = QFontMetrics(ui_font())
    measure = metrics.horizontalAdvance

    section("truncate_text — 像素级截断")
    short = 'ab.mp4'
    check("短文本原样返回", truncate_text(short, 250, measure) == short,
          f"got={truncate_text(short, 250, measure)!r}")

    long_ascii = 'a' * 200
    got = truncate_text(long_ascii, 100, measure)
    check("超长 ASCII 被截断", len(got) < len(long_ascii), f"len={len(got)}")
    check("截断结果以 ... 结尾", got.endswith('...'), f"got={got[-10:]!r}")
    check("截断结果不超列宽", measure(got) <= 100 - 10,
          f"w={measure(got)}")

    long_cjk = '中文标题' * 40
    got = truncate_text(long_cjk, 100, measure)
    check("超长 CJK 被截断", measure(got) <= 100 - 10,
          f"w={measure(got)}")

    got = truncate_text('anything', 5, measure)
    check("列宽 < 10 → 返回 '...'", got == '...', f"got={got!r}")

    got = truncate_text('', 250, measure)
    check("空串原样返回", got == '', f"got={got!r}")

    # 快路径的**上界估算** (video_core.truncate_text 里的 per_char) 必须
    # >= 真实**最宽**字符宽 —— 否则快路径会放过超宽文本, 而 Qt 的 elide
    # 是关的 (TruncatingDelegate 设的是 Qt.ElideNone) → 文字直接溢出列宽。
    #
    # 2026-10-29 返工轮 1: per_char 8/16 → **13/16** (ASCII 取可打印
    # 字符里**最宽**的 'W' = 13px)。之前那版注释写的「8/16 是真上界」是
    # **错的** —— 8 只是**平均**宽 ('a' 才 7px), 而 'W' 有 13px → 一串
    # 'W' 会被快路径当成「装得下」放行, 真实宽 13px/字 → 溢出列宽。
    #
    # 这条断言钉的是**不变量**「快路径返回的文本一定装得下」, **不是**
    # per_char 这个常量本身 —— 换字体/换字号把常量作废时这里要红。
    # 所以样本里**必须带宽字形** ('W' / 'w'): 只测 'a' 与 CJK 的话,
    # 测的恰好是从来不会出事的两个 case (7px / 13px, 都远小于上界)。
    overflow = []
    for n in range(1, 120):
        for text in ('a' * n, 'W' * n, 'w' * n, '中文标题' * n):
            res = truncate_text(text, 250, measure)
            if measure(res) > 250 - 10:
                overflow.append((n, measure(res)))
    check("快路径也不放过超宽文本 (估算上界 >= 真实最宽字符宽)",
          not overflow, f"got={overflow[:5]}")

    section("状态栏合并成一行 (状态 + 统计)")
    # 原断言 (Tk 版): 建一个 VideoCheckerApp, 看状态栏那一行字。
    # Qt 版对应: video_qt.MainWindow —— 状态栏是 QStatusBar, 文案走
    # currentMessage()。
    app = MainWindow()
    app.show()
    qt_app.processEvents()
    check("初始文案是「就绪」",
          app.status_bar.currentMessage() == '就绪',
          f"got={app.status_bar.currentMessage()!r}")
    check("不再有独立的 count_var (两处合一行)",
          not hasattr(app, 'count_var'),
          f"got={getattr(app, 'count_var', None)!r}")

    app._add_result(make_info(title='p.mp4', is_passing=True))
    app._add_result(make_info(title='f.mp4', is_passing=False))
    app._add_result(make_info(title='p2.mp4', is_passing=True))
    qt_app.processEvents()
    check("中间态不拼统计 (扫描中只显示进度文字)",
          app.status_bar.currentMessage() == '就绪',
          f"got={app.status_bar.currentMessage()!r}")

    app.total_files = 3
    app._scan_complete()
    qt_app.processEvents()
    check("完成文案合并了统计",
          app.status_bar.currentMessage()
          == '检测完成：共 3 · 达标 2 · 不达标 1',
          f"got={app.status_bar.currentMessage()!r}")

    app._clear_records()
    qt_app.processEvents()
    check("清除记录后归零",
          app.status_bar.currentMessage()
          == '已清除记录：共 0 · 达标 0 · 不达标 0',
          f"got={app.status_bar.currentMessage()!r}")

    section("状态栏 — 0 文件完成时计数归零")
    app.total_files = 0
    app._scan_complete()
    qt_app.processEvents()
    # 2026-10-27 起空目录改显示「未找到视频文件」(不带全是 0 的统计) ——
    # 旧文案「检测完成：共 0 · 达标 0 · 不达标 0」是废话, 而且把 worker
    # 早就发过的那条「未找到视频文件」盖掉了。
    check("0 文件后计数不残留",
          app.status_bar.currentMessage() == '未找到视频文件',
          f"got={app.status_bar.currentMessage()!r}")

    section("状态栏 — 中途停止")
    app._add_result(make_info(title='a.mp4', is_passing=True))
    app.total_files = 10
    app._cancel.set()
    app._scan_complete()
    qt_app.processEvents()
    check("停止文案带统计且只有一个冒号",
          app.status_bar.currentMessage()
          == '已停止检测（已完成 1/10）：共 1 · 达标 1 · 不达标 0',
          f"got={app.status_bar.currentMessage()!r}")
    app._cancel.clear()

    # brief 漏掉的路径: 开始检测 / 拖拽也 clear 了 video_results,
    # 也重置了 _passed_count。
    #
    # **更正 (2026-10-29)**: 旧注释写的是「但**必须**同时重置
    # _passed_count —— 扫第二轮会累加上一轮的达标数」—— **这个前提是
    # 错的**, 不要照着它去理解代码:
    #   状态栏的「达标 N」**不是**一个跨轮次累加的运行计数器 —— 它是每轮
    #   扫描结束时由 _scan_complete (video_qt.py:1653-1655) **从
    #   video_results 重新算**出来的 (sum(1 for v in ... if v.is_passing)),
    #   算完还顺手回写 self._passed_count。所以「扫第二轮会累加上一轮的
    #   达标数」这件事**根本不会发生**。
    #   → _begin_scan 里那句 self._passed_count = 0 因此**不是
    #   load-bearing** 的, 它只是双保险 (让扫描过程中 _add_result 的
    #   自增也不带着上一轮的值), 删掉它不会让状态栏的计数出错。
    #   别把它当成「漏了这一行就会出 bug」的必需行。
    #
    # 2026-10-27: 这条**原来**是用**源码文本**锚的 (「clear() 之后 320 个
    # 字符内必须出现 `self._passed_count = 0`」+「两条入口都得走
    # _begin_scan」)。源码文本断言有个致命的毛病: 任何无害重构都会打断
    # 它, 而它守的又是一个**真行为** —— 从来没有脚本**端到端**测过。
    # 现在换成**行为**测试: 真扫一遍 → 再扫一遍 → 断言「共 N」不累加。
    # 两条入口 (点「开始检测」/ 拖拽) 各测一遍 —— 少一条就可能漏掉归零。
    section("扫第二轮: 达标计数不累加 (行为测试)")
    scan_src = mkdtemp(prefix='vdcheck_ui_scan_')
    scan_file = os.path.join(scan_src, 'a.mp4')
    open(scan_file, 'w').close()
    real_run_ffprobe = video_qt.run_ffprobe
    video_qt.run_ffprobe = lambda path: fake_ffprobe_json()
    try:
        win_scan = MainWindow()
        win_scan.path_edit.setText(scan_src)
        win_scan._start_scan()
        check("第一轮扫完了", wait_scan(win_scan))
        check("第一轮: 共 1 · 达标 1 · 不达标 0",
              win_scan.status_bar.currentMessage()
              == '检测完成：共 1 · 达标 1 · 不达标 0',
              f"got={win_scan.status_bar.currentMessage()!r}")
        # **再扫一遍** —— 计数必须从头来, 不能带着上一轮的达标数累加
        # (漏了归零就会变成「共 1 · 达标 2 · 不达标 -1」)
        win_scan._start_scan()
        check("第二轮扫完了", wait_scan(win_scan))
        check("第二轮: 计数**不累加** (仍是共 1 · 达标 1)",
              win_scan.status_bar.currentMessage()
              == '检测完成：共 1 · 达标 1 · 不达标 0',
              f"got={win_scan.status_bar.currentMessage()!r}")
        check("第二轮: 表格里也只有 1 行 (结果列表也清了)",
              win_scan.model.rowCount() == 1,
              f"got={win_scan.model.rowCount()}")

        # 拖拽那条入口 (Qt 版里点按钮与拖拽收敛到同一条 _begin_scan 路径,
        # 但两条入口各自都得验 —— 少一条就可能漏掉归零)
        win_drop = MainWindow()
        win_drop._on_file_drop([scan_file])
        check("拖拽第一轮扫完了", wait_scan(win_drop))
        check("拖拽第一轮: 共 1 · 达标 1 · 不达标 0",
              win_drop.status_bar.currentMessage()
              == '检测完成：共 1 · 达标 1 · 不达标 0',
              f"got={win_drop.status_bar.currentMessage()!r}")
        win_drop._on_file_drop([scan_file])
        check("拖拽第二轮扫完了", wait_scan(win_drop))
        check("拖拽第二轮: 计数**不累加** (仍是共 1 · 达标 1)",
              win_drop.status_bar.currentMessage()
              == '检测完成：共 1 · 达标 1 · 不达标 0',
              f"got={win_drop.status_bar.currentMessage()!r}")
        check("拖拽第二轮: 表格里也只有 1 行",
              win_drop.model.rowCount() == 1,
              f"got={win_drop.model.rowCount()}")
    finally:
        video_qt.run_ffprobe = real_run_ffprobe

    # 原先这里还有两节 Tooltip 断言 (「只在截断时显示」/「<Leave> 延迟关闭 +
    # 窗口复用」) —— 它们测的是 Tk 版自绘的 Tooltip 类 (延迟关闭 / 窗口复用 /
    # lift() 而非 -topmost)。Qt 版**整个删掉**这个类, 改用 Qt 内置 tooltip
    # (设计文档 §5.6), Task 7 会把 Tooltip 从 video_checker.py 里删掉 ——
    # 留着这两节的话, 那个脚本到时 import 失败。
    #
    # 搬走后的落点 (语义等价, 见
    # docs/superpowers/specs/2026-10-27-qt-table-assertions.py):
    #   「tooltip: 只在被截断时给完整值」        —— 截断才给完整值, 否则 None
    #   「tooltip — 视图真的向模型要完整值」      —— 真实事件派发, 视图确实问模型
    #   「表头点击的边界」                        —— clear() 之后 tooltip 也不留陈旧值
    # 没有等价物的部分 (Tk Tooltip 的窗口复用 / 延迟关闭 / 不抢焦点):
    #   Qt 的 tooltip 窗口由 Qt 自己管, 没有「复用」这个概念 —— 报告 §4.1 已列明。

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()
