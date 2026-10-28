# -*- mode: python ; coding: utf-8 -*-
import os


def get_ffprobe_data():
    """获取项目中的 ffprobe 目录（包含 exe 和所有 DLL）"""
    datas = []
    spec_dir = os.path.dirname(os.path.abspath(SPEC))
    ffprobe_dir = os.path.join(spec_dir, 'ffprobe')
    if os.path.exists(ffprobe_dir):
        datas.append((ffprobe_dir, 'ffprobe'))
    return datas


# ------------------------------------------------------------------
# PySide6 里本 app 用不到的模块 —— 逐个排除
# ------------------------------------------------------------------
#
# 本 app 只 import PySide6.QtCore / QtGui / QtWidgets 三个模块:
#   实测 import 这三个之后, sys.modules 里的 PySide6.* 只有这三个
#   加上 PySide6.support.* —— 没有任何别的 PySide6 模块被拉进来。
#
# 这三个模块的**链接期依赖**也只有 QtCore / QtGui / QtWidgets:
#   实测 otool -L QtWidgets.abi3.so  -> QtWidgets/QtGui/QtCore.framework
#   实测 otool -L QtWidgets.framework -> QtGui.framework + QtCore.framework
#   实测 otool -L QtGui.framework     -> QtCore.framework (+ macOS 的 QtDBus)
#   **没有 QtNetwork** —— 所以 QtNetwork 不在 QtWidgets 的依赖链上,
#   排掉它不会让 QtWidgets 起不来 (这一点与「QtNetwork 会被 QtWidgets
#   依赖」的旧猜测相反, 已实测推翻)。
#
# 下面这份清单 = 「本 app 明确不用」∩「不在这条链接链上」。排掉它们既是
# 省体积, 也是防将来误 import 时悄悄把 exe 撑大。
#
# **刻意没排的几组** (排了体积收益≈0、风险不为0):
#   PySide6.QtSvg / QtSvgWidgets  —— imageformats 插件目录里的 qsvg 插件
#                                    要 QtSvg.dll, 而插件目录是随 QtGui
#                                    一起收的; 排 Python 模块不影响它。
#   PySide6.QtOpenGL / QtOpenGLWidgets
#   PySide6.QtPrintSupport
#   PySide6.QtDBus                —— macOS 上 QtGui.framework 直接链它。
#
# **未验证项**: 本机是 macOS, 跑不了 Windows 的 exe。排错模块的代价是
# 「exe 启动即崩」, 所以这份清单只按「实测过链接链」的标准保守地挑 ——
# 仍需在 Windows 上跑一次 exe 冒烟 (启动 + 扫一个目录) 才算完。
PYSIDE6_EXCLUDES = [
    # --- 体积大头, 本 app 完全用不到 ---
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebChannel',
    'PySide6.QtWebSockets',
    'PySide6.QtWebView',
    'PySide6.QtQuick',
    'PySide6.QtQuick3D',
    'PySide6.QtQuickControls2',
    'PySide6.QtQuickTest',
    'PySide6.QtQuickWidgets',
    'PySide6.QtQml',
    'PySide6.QtQmlFeatures',
    'PySide6.Qt3DAnimation',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
    'PySide6.Qt3DRender',
    'PySide6.QtCharts',
    'PySide6.QtDataVisualization',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtPdf',
    'PySide6.QtPdfWidgets',
    # --- 网络: 本 app 不联网, 且 QtWidgets **不**依赖它 (实测) ---
    'PySide6.QtNetwork',
    'PySide6.QtNetworkAuth',
    # --- 其余用不到的 ---
    'PySide6.QtBluetooth',
    'PySide6.QtCanvasPainter',
    'PySide6.QtConcurrent',
    'PySide6.QtDesigner',
    'PySide6.QtGraphs',
    'PySide6.QtGraphsWidgets',
    'PySide6.QtHelp',
    'PySide6.QtHttpServer',
    'PySide6.QtLabsStyleKit',
    'PySide6.QtLocation',
    'PySide6.QtNfc',
    'PySide6.QtPositioning',
    'PySide6.QtRemoteObjects',
    'PySide6.QtScxml',
    'PySide6.QtSensors',
    'PySide6.QtSerialBus',
    'PySide6.QtSerialPort',
    'PySide6.QtSpatialAudio',
    'PySide6.QtSql',
    'PySide6.QtStateMachine',
    'PySide6.QtTest',
    'PySide6.QtTextToSpeech',
    'PySide6.QtUiTools',
    'PySide6.QtXml',
]


a = Analysis(
    ['video_checker.py'],
    pathex=[],
    binaries=[],
    datas=get_ffprobe_data(),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=PYSIDE6_EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='视频码率检查器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
