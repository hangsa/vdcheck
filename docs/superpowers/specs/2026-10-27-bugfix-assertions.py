"""三个既有缺陷的回归断言。

1. run_ffprobe 在非 Windows 上无条件构造 subprocess.STARTUPINFO ——
   macOS/Linux 上 AttributeError 逃出 except 列表, worker 线程静默死亡,
   UI 永久卡在「检测中」。实际后果: 这个 app 只能在 Windows 上跑。
2. 阈值解析接受 inf / nan (float() 通过且 > 0 成立) → 所有行永远不达标。
3. 「未找到视频文件」的状态文案被紧随其后的完成逻辑立刻覆盖, 用户永远看不到。

跑法: python docs/superpowers/specs/2026-10-27-bugfix-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

# Qt 版主窗口要 QApplication 才能建 (QFontMetrics 没有 QGuiApplication 会
# 直接 abort 进程), 所以先建 app 再 import video_qt。没有显示环境时用
# offscreen 平台。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

_failures: list[str] = []


def check(name, condition, detail=""):
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title):
    print(f"\n=== {title} ===")


class _MsgStub:
    """替掉 messagebox —— 真弹窗是模态的, 会把脚本卡住。"""

    def __init__(self):
        self.errors = []

    def showerror(self, title, message=None):
        self.errors.append((title, message))

    def showinfo(self, title, message=None):
        pass

    def showwarning(self, title, message=None):
        pass

    def askyesno(self, *args, **kwargs):
        return False


def main():
    section("阈值必须是有限的正数 (真调 app 的校验函数)")
    # 单独 try: 少了这个函数时也要把后面几节跑完, 免得第一个 ImportError
    # 就把整个脚本带崩 —— 崩掉看不出「另外两个缺陷也没修」。
    try:
        from video_core import parse_threshold
    except ImportError as e:
        parse_threshold = None
        check("video_core 导出 parse_threshold", False, f"import 失败: {e}")
    if parse_threshold is not None:
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
    # 原断言 (Tk 版): 建一个 VideoCheckerApp, total_files 清零, 调
    # _scan_complete, 看状态栏是不是「未找到视频文件」。
    # Qt 版对应: video_qt.MainWindow (状态栏是 QStatusBar, 文案走
    # currentMessage())。
    from video_qt import MainWindow
    app = MainWindow()
    app.total_files = 0
    app._scan_complete()
    _app.processEvents()
    check("显示「未找到视频文件」",
          app.status_bar.currentMessage() == '未找到视频文件',
          f"got={app.status_bar.currentMessage()!r}")

    section("app 的阈值入口拒绝 inf / nan (真调 app, 文案不变)")
    # 光测纯函数不够 —— 还得证明 app 真的走了这个入口, 且弹的是原来那句文案。
    tmp_dir = tempfile.mkdtemp()
    stub = _MsgStub()
    # 弹窗是模态的, 会把脚本卡住 —— 换成记录器。
    app._show_error = lambda text: stub.showerror('错误', text)
    try:
        app.path_edit.setText(tmp_dir)   # 先给个有效目录, 否则先撞路径校验
        # 哨兵: 阈值过了之后 _start_scan 会把 total_files 清零并起扫描线程,
        # 所以「没被清零」= 校验没过就 return 了 (比看 scanning 标志可靠:
        # 空目录的扫描瞬间就结束, scanning 会被 _scan_complete 改回 False)。
        app.total_files = 123
        app.bitrate_edit.setText('inf')
        app._start_scan()
        check("inf 被 app 拒绝, 文案不变",
              stub.errors == [("错误", "码率标准必须为正数。")],
              f"got={stub.errors!r}")
        check("inf 时没继续往下走", app.total_files == 123,
              f"got={app.total_files!r}")
        stub.errors.clear()
        app.total_files = 123
        app.bitrate_edit.setText('30000')
        app.sample_rate_edit.setText('nan')
        app._start_scan()
        check("nan 被 app 拒绝, 文案不变",
              stub.errors == [("错误", "采样率标准必须为正数。")],
              f"got={stub.errors!r}")
        check("nan 时没继续往下走", app.total_files == 123,
              f"got={app.total_files!r}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 原先这里还有一节「类属性与 core 常量同源 (不能各抄一份字面量)」——
    # 断言 VideoGridView.FG_PASS / FG_FAIL 就是 core 的那两个常量。
    # 表格 GUI 层换成 Qt 之后, VideoGridView 这个类没了 (Task 7 删),
    # 同一个不变量的 Qt 形态 (界面层不持有自己的颜色常量、不硬抄字面量)
    # 改到 docs/superpowers/specs/2026-10-27-qt-table-assertions.py 的
    # 「着色常量只有一个来源」那一节去断言了。

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
