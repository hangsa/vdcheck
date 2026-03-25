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
    echo     ffprobe 未找到，正在下载 FFmpeg...
    :: 下载 FFmpeg（包含所有 DLL）
    curl -L -o ffmpeg.zip "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    powershell -Command "Expand-Archive -Path ffmpeg.zip -DestinationPath . -Force"
    :: 复制 ffprobe.exe 和所有 DLL
    for /r "ffmpeg-master-latest-win64-gpl\bin" %%i in (*.*) do (
        copy "%%i" ffprobe\ >nul
    )
    :: 清理
    rmdir /s /q ffmpeg-master-latest-win64-gpl 2>nul
    del ffmpeg.zip 2>nul
    if not exist ffprobe\ffprobe.exe (
        echo [错误] FFmpeg 下载失败，请手动下载并放入 ffprobe\ffprobe.exe
        echo     下载地址: https://ffmpeg.org/download.html
        pause
        exit /b 1
    )
    echo     FFmpeg 下载完成
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
