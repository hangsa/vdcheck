"""界面信息展示效率优化 一次性断言脚本。

验证：
- format_title 全部 rel_path 形态 (含跨盘符绝对路径 / UNC);
- truncate_text 像素级截断 (ASCII / CJK / 边界);
- 状态栏文案 (一行合并 + 计数归零 + 中途停止)。

**表格相关的小节已迁走** (2026-10-27, Tk → Qt 重构):
`_format_cell` (title/spec)、表头带单位、10 列集合、`_truncate_text` 委托、
网格 cell 的 `_truncated` 标记 —— 这些断言的是 Tk 的 VideoGridView 内部结构,
对应的语义改测 Qt 模型, 见
`docs/superpowers/specs/2026-10-27-qt-table-assertions.py`。

**定点截图弹窗的小节也迁走了** (2026-10-27, 同一个 Tk → Qt 重构):
横向滚动 / 表头与数据列对齐 / 空文件列表 / 标题限宽 / 缩略图只留 320px /
滚轮绑定 —— 前六项对应的语义见
`docs/superpowers/specs/2026-10-27-qt-snapshot-assertions.py`;
**滚轮绑定那节直接删掉, 不补 Qt 版** —— 逐 widget 绑 `<MouseWheel>` 是
Tk 事件派发机制的产物, `QTableView` 原生支持滚轮, 不需要逐 widget 绑。

另外两处**行为差异** (不是 bug, 是构造方式不同带来的):
- 旧 Tk 弹窗把表头做成 grid 里的一个 widget, 纵向滚动时随内容滚走 (不钉住);
  Qt 的表头本来就是独立控件, 天然钉在顶部。
- 旧 Tk 弹窗的 `mark_done` 用 `PhotoImage.subsample(2,2)` 得到 320px 的
  display, 640px 原图出作用域即回收; Qt 版同样只留 320px, 放大窗口从磁盘
  重新加载 640px 原图。

运行: python docs/superpowers/specs/2026-10-23-ui-density-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import atexit
import os
import shutil
import sys
import tempfile

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

import tkinter as tk
from tkinter import font as tkfont

from video_checker import VideoInfo, format_title, truncate_text

# Qt 版主窗口那几节要 QApplication (QFontMetrics 没有 QGuiApplication 会
# 直接 abort 进程)。没有显示环境时 Qt 用 offscreen 平台; Tk 那边不受影响。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


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


def source_text() -> str:
    """读整个 video_checker.py 源码 (原样, 不做行尾转换)。"""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), 'video_checker.py')
    # newline='' 必须: 源码的行尾不能被 Python 转换, 下标/正则才对得上源文件
    return open(path, encoding='utf-8', newline='').read()


def method_body(method: str) -> str:
    """截出某个方法的源码 (从 'def <method>(self' 到下一个同缩进的 def)。"""
    src = source_text()
    start = src.index(f"    def {method}(self")
    rest = src[start + 1:]
    end = rest.index("\n    def ")
    return src[start:start + 1 + end]


def qt_method_body(method: str) -> str:
    """截出 video_qt.py 里某个方法的源码。"""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), 'video_qt.py')
    src = open(path, encoding='utf-8', newline='').read()
    start = src.index(f"    def {method}(self")
    rest = src[start + 1:]
    end = rest.index("\n    def ")
    return src[start:start + 1 + end]


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

    root = tk.Tk()
    root.withdraw()
    font = tkfont.Font(root=root, font=('TkDefaultFont', 9))

    section("truncate_text — 像素级截断")
    short = 'ab.mp4'
    check("短文本原样返回", truncate_text(short, 250, font.measure) == short,
          f"got={truncate_text(short, 250, font.measure)!r}")

    long_ascii = 'a' * 200
    got = truncate_text(long_ascii, 100, font.measure)
    check("超长 ASCII 被截断", len(got) < len(long_ascii), f"len={len(got)}")
    check("截断结果以 ... 结尾", got.endswith('...'), f"got={got[-10:]!r}")
    check("截断结果不超列宽", font.measure(got) <= 100 - 10,
          f"w={font.measure(got)}")

    long_cjk = '中文标题' * 40
    got = truncate_text(long_cjk, 100, font.measure)
    check("超长 CJK 被截断", font.measure(got) <= 100 - 10,
          f"w={font.measure(got)}")

    got = truncate_text('anything', 5, font.measure)
    check("列宽 < 10 → 返回 '...'", got == '...', f"got={got!r}")

    got = truncate_text('', 250, font.measure)
    check("空串原样返回", got == '', f"got={got!r}")

    section("状态栏合并成一行 (状态 + 统计)")
    # 原断言 (Tk 版): 建一个 VideoCheckerApp, 看状态栏那一行字。
    # Qt 版对应: video_qt.MainWindow —— 状态栏是 QStatusBar, 文案走
    # currentMessage()。QApplication 必须**先于** video_qt 建好。
    from PySide6.QtWidgets import QApplication
    qt_app = QApplication.instance() or QApplication(sys.argv)
    from video_qt import MainWindow
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
    # 但必须同时重置 _passed_count —— 扫第二轮会累加上一轮的达标数,
    # 状态栏出现「达标 188 · 不达标 -178」。真跑这两个方法要起线程调
    # ffprobe, 太重; 退一步锚源码。
    # Qt 版把「清空结果 + 计数归零」收敛在 _begin_scan 里 (点按钮与拖拽
    # 共用这一条路径), 所以: ① _begin_scan 里 clear() 之后必须紧跟计数
    # 归零; ② 两条入口都得走 _begin_scan —— 少一条就可能漏掉归零。
    body = qt_method_body('_begin_scan')
    at = body.index('self.video_results.clear()')
    window = body[at:at + 320]
    ok = ('self._passed_count = 0' in window)
    # check() 无条件打印 detail, 通过时别塞一句像报错的文案
    check("_begin_scan 清空结果时同步重置计数", ok,
          "" if ok else "clear() 之后的窗口里没找到 _passed_count = 0")
    for meth in ('_start_scan', '_on_file_drop'):
        ok = ('_begin_scan(' in qt_method_body(meth))
        check(f"{meth} 走 _begin_scan (计数归零不会漏)", ok,
              "" if ok else f"{meth} 里没找到 _begin_scan(")

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

    root.destroy()

    print(f"\n{'FAIL' if _failures else 'PASS'}: {len(_failures)} failure(s)")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()