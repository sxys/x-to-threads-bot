# register_daily_task.ps1
# 在 Windows 任务计划程序中创建每日早上 08:00 自动执行的任务

$TaskName = "XTopDailyTelegramBot"
$WorkingDir = "D:\abel\testGravity"
$PythonExe = "$WorkingDir\.venv\Scripts\python.exe"
$ScriptPath = "$WorkingDir\x_top_bot.py"
$Arguments = "$ScriptPath --run-now"

Write-Host "正在注册 Windows 每日定时任务: $TaskName ..." -ForegroundColor Cyan

# 1. 触发器：每天早上 08:00 触发
$Trigger = New-ScheduledTaskTrigger -Daily -At 08:00AM

# 2. 动作：调用虚拟环境 python 运行抓取
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $Arguments -WorkingDirectory $WorkingDir

# 3. 设置：如果错过时间尽快补跑、网络可用时执行
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

# 4. 注册任务
try {
    # 如果已存在同名任务，先注销
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Trigger $Trigger -Action $Action -Settings $Settings -Description "每天早上08:00抓取X主页Top20纯中文热门推文并发送到Telegram" | Out-Null
    Write-Host "✅ 定时任务已成功创建！" -ForegroundColor Green
    Write-Host "任务名称: $TaskName"
    Write-Host "执行时间: 每天 08:00 AM"
    Write-Host "执行命令: $PythonExe $Arguments"
} catch {
    Write-Host "❌ 注册任务失败: $_" -ForegroundColor Red
}
