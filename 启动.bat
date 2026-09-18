@echo off
title BiliScribe - B站字幕本地提取
cd /d "%~dp0"

echo ============================================
echo   BiliScribe  B站字幕本地提取工具
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Python，请先安装 Python 3.10 及以上版本：
  echo https://www.python.org/downloads/  安装时请勾选 Add Python to PATH
  pause
  exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo [错误] 当前 Python 版本低于 3.10，本工具需要 Python 3.10 及以上版本。
  echo 请升级后重试: https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] 首次运行，正在创建虚拟环境...
  python -m venv .venv
  if errorlevel 1 (
    echo [错误] 虚拟环境创建失败
    pause & exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
python -c "import httpx,fastapi,uvicorn,qrcode,PIL" 2>nul
if errorlevel 1 (
  echo [2/2] 首次运行，正在安装依赖（需联网，请稍候 1-3 分钟）...
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重新双击本脚本
    pause & exit /b 1
  )
)

echo 正在启动本地控制台，浏览器将自动打开 http://127.0.0.1:8765
echo 若 8765 端口被占用，会自动更换端口，请注意本窗口中的实际地址。
echo 关闭此黑窗口即停止服务。
echo.
python -m biliscribe web
pause
