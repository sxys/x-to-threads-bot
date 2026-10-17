@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo         Threads 每日自动定时发布守护服务
echo ========================================================
echo.
echo 正在启动 Threads 自动发布器 (每天 08:00 - 23:00 均匀发布)...
echo 可随时查看当前队列状态: python threads_poster.py --status
echo.

.\.venv\Scripts\python.exe threads_poster.py
pause
