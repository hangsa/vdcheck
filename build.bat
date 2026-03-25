@echo off
chcp 65001 >nul
echo ========================================
echo   视频码率检查器 - 构建脚本
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

:: 检查 ffprobe
echo [1/4] 检查 ffprobe...
if not exist ffprobe mkdir ffprobe
if not exist ffprobe\ffprobe.exe (
    echo     ffprobe 未找到，正在下载...
    :: 使用 winget 安装 ffmpeg（包含 ffprobe）
    winget install ffmpeg --accept-package-agreements --accept-source-agreements -h
    :: 复制 ffprobe.exe 到项目目录
    set FFPROBE_PATH=
    for /r "%LOCALAPPDATA%\Microsoft\WindowsApps" %%i in (ffprobe.exe) do (
        if exist "%%i" (
            copy "%%i" ffprobe\ffprobe.exe >nul
            goto :ffprobe_copied
        )
    )
    :ffprobe_copied
    if not exist ffprobe\ffprobe.exe (
        echo [错误] ffprobe 下载失败，请手动下载并放入 ffprobe\ffprobe.exe
        echo     下载地址: https://ffmpeg.org/download.html
        pause
        exit /b 1
    )
    echo     ffprobe 下载完成
)

:: 安装依赖
echo [2/4] 安装依赖...
pip install tkinterdnd2 pyinstaller -q
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

:: 清理旧构建
echo [3/4] 清理旧构建文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

:: 执行构建
echo [4/4] 开始构建...
pyinstaller video_checker.spec
if errorlevel 1 (
    echo [错误] 构建失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo   构建完成！
echo   可执行文件位于: dist\视频码率检查器.exe
echo ========================================
pause
