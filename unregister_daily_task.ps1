# unregister_daily_task.ps1
# 移除 Windows 每日定时任务

$TaskName = "XTopDailyTelegramBot"

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "✅ 已成功移除 Windows 定时任务: $TaskName" -ForegroundColor Green
} catch {
    Write-Host "提示: 未找到名为 $TaskName 的定时任务或已被删除。" -ForegroundColor Yellow
}
