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

:: 安装依赖
echo [1/3] 安装依赖...
pip install tkinterdnd2 pyinstaller -q
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

:: 清理旧构建
echo [2/3] 清理旧构建文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

:: 执行构建
echo [3/3] 开始构建...
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
