# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import site


def get_tkinterdnd2_data():
    """获取 tkinterdnd2 包的数据路径"""
    datas = []
    for p in site.getsitepackages():
        src = os.path.join(p, 'tkinterdnd2')
        if os.path.exists(src):
            datas.append((src, 'tkinterdnd2'))
            break
    return datas


a = Analysis(
    ['video_checker.py'],
    pathex=[],
    binaries=[],
    datas=get_tkinterdnd2_data(),
    hiddenimports=['tkinterdnd2', 'tkdnd'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
