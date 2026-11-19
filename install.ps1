# install.ps1 — Input Guard 一键安装 / 重新配置 / 卸载（会自动请求管理员权限）
#
#   powershell -ExecutionPolicy Bypass -File install.ps1                       # 默认 Ctrl+Alt+L
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Hotkey "ctrl+shift+f12"
#   powershell -ExecutionPolicy Bypass -File install.ps1 -SetPassword          # 重设密码
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
#
# 做的事：
#   1. 还没设密码（或带 -SetPassword）时，弹出一个窗口让用户亲手输入密码；脚本和 agent 都碰不到明文
#   2. 建两个最高权限的计划任务，之后不弹 UAC、不出窗口：
#        InputGuard Hotkey  登录时启动热键守护
#        InputGuard Lock    立即上锁一次（桌面 InputGuard.vbs 调它）
#   3. 在桌面放 InputGuard.vbs（双击立即上锁），可用 -NoDesktopIcon 跳过
#   4. 立即启动热键守护
# 结果写到 %USERPROFILE%\input_guard_install.txt，退出码 0 = 成功

param(
    [string]$Hotkey = 'ctrl+alt+l',
    [string]$QuitHotkey = 'ctrl+alt+q',
    [switch]$SetPassword,
    [switch]$NoDesktopIcon,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # 正确读取 Python 输出的中文
$here       = Split-Path -Parent $PSCommandPath
$resultFile = Join-Path $env:USERPROFILE 'input_guard_install.txt'
$taskNames  = @('InputGuard Hotkey', 'InputGuard Lock')
$desktopVbs = Join-Path ([Environment]::GetFolderPath('Desktop')) 'InputGuard.vbs'

# ---- 自动提权：非管理员时以管理员身份重启自己并等待结果 ----
$isAdmin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
           '-Hotkey', "`"$Hotkey`"", '-QuitHotkey', "`"$QuitHotkey`"")
    if ($SetPassword)   { $a += '-SetPassword' }
    if ($NoDesktopIcon) { $a += '-NoDesktopIcon' }
    if ($Uninstall)     { $a += '-Uninstall' }
    Remove-Item $resultFile -ErrorAction SilentlyContinue
    try {
        $p = Start-Process powershell -Verb RunAs -ArgumentList $a -Wait -PassThru
    } catch {
        Write-Host '已取消：需要在 UAC 提示里点「是」。'
        exit 2
    }
    if (Test-Path $resultFile) { Get-Content $resultFile -Encoding UTF8 }
    exit $p.ExitCode
}

function Finish([int]$code, [string]$msg) {
    Set-Content -Path $resultFile -Value $msg -Encoding UTF8
    Write-Host $msg
    Start-Sleep -Seconds 3
    exit $code
}

function Stop-Daemon {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like '*hotkey_guard.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

try {
    foreach ($n in $taskNames) {
        Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue
    }
    Stop-Daemon

    if ($Uninstall) {
        Remove-Item $desktopVbs -ErrorAction SilentlyContinue
        Finish 0 '已卸载：计划任务和桌面图标已删除。密码文件 ~/.input_guard.json 和日志保留。'
    }

    # ---- 找 Python ----
    $py = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $py = (& py -3 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
    }
    if (-not $py -and (Get-Command python -ErrorAction SilentlyContinue)) {
        $py = (& python -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
    }
    if (-not $py -or -not (Test-Path $py)) { Finish 1 '失败：找不到 Python 3。先装 Python（python.org），再重跑。' }
    $pyw = Join-Path (Split-Path $py) 'pythonw.exe'
    if (-not (Test-Path $pyw)) { Finish 1 "失败：找不到 $pyw" }

    # ---- 检查热键写法 ----
    foreach ($hk in @($Hotkey, $QuitHotkey)) {
        if ($hk -eq 'none') { continue }
        $out = & $py (Join-Path $here 'hotkey_guard.py') --check $hk
        if ($LASTEXITCODE -ne 0) { Finish 1 "失败：热键 '$hk' 无效：$out" }
    }

    # ---- 密码：弹窗让用户亲手输入 ----
    $cfg = Join-Path $env:USERPROFILE '.input_guard.json'
    if ($SetPassword -or -not (Test-Path $cfg)) {
        Start-Process -FilePath $py -ArgumentList @("`"$(Join-Path $here 'input_guard.py')`"", '--set-password') -Wait
        if (-not (Test-Path $cfg)) { Finish 1 '失败：密码没有设好（窗口被关掉或两次输入不一致）。重跑安装即可。' }
    }

    # ---- 计划任务 ----
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $prin = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    # ExecutionTimeLimit 0 = 不限时（默认 72 小时会被系统杀掉）
    $set  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

    $hotArgs   = "`"$(Join-Path $here 'hotkey_guard.py')`" --hotkey `"$Hotkey`" --quit-hotkey `"$QuitHotkey`""
    $hotAction = New-ScheduledTaskAction -Execute $pyw -Argument $hotArgs -WorkingDirectory $here
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
    Register-ScheduledTask -TaskName 'InputGuard Hotkey' -Action $hotAction -Trigger $trigger `
        -Principal $prin -Settings $set | Out-Null

    $lockAction = New-ScheduledTaskAction -Execute $pyw -Argument "`"$(Join-Path $here 'run_lock_hidden.pyw')`"" `
                      -WorkingDirectory $here
    Register-ScheduledTask -TaskName 'InputGuard Lock' -Action $lockAction -Principal $prin -Settings $set | Out-Null

    # ---- 桌面图标 ----
    if (-not $NoDesktopIcon) {
        $vbs = @(
            "' InputGuard.vbs - double-click to lock now (no window, no countdown, no UAC).",
            'Dim rc',
            'rc = CreateObject("WScript.Shell").Run("schtasks /run /tn ""InputGuard Lock""", 0, True)',
            'If rc <> 0 Then MsgBox "InputGuard is not installed. Run install.ps1 first.", 48, "InputGuard"'
        )
        Set-Content -Path $desktopVbs -Value $vbs -Encoding ASCII
    }

    # ---- 启动并确认 ----
    Start-ScheduledTask -TaskName 'InputGuard Hotkey'
    Start-Sleep -Seconds 2
    $running = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
                   Where-Object { $_.CommandLine -like '*hotkey_guard.py*' }
    if (-not $running) {
        $tail = Get-Content (Join-Path $env:USERPROFILE 'input_guard_log.txt') -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
        Finish 1 "失败：热键守护没有起来。日志最后一行：$tail"
    }
    Finish 0 "安装完成：$Hotkey 立即上锁，盲打密码解锁；登录后自动生效。"
} catch {
    Finish 1 "失败：$($_.Exception.Message)"
}
