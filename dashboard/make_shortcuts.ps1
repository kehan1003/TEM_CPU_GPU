# Creates the three no-console shortcuts (Monitor / Stop / Temp-service).
# Saved as UTF-8 **with BOM** on purpose: Windows PowerShell 5.1 would otherwise
# read this file as ANSI and mangle the Chinese file names.
param([string]$Pythonw)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

if (-not $Pythonw) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $Pythonw = Join-Path (Split-Path $cmd.Source) "pythonw.exe" }
}
if (-not $Pythonw -or -not (Test-Path $Pythonw)) {
    Write-Host "[x] pythonw.exe not found: $Pythonw"
    exit 1
}

$launch = Join-Path $here "launch.py"
if (-not (Test-Path $launch)) {
    Write-Host "[x] launch.py not found next to this script: $launch"
    exit 1
}

$sh = New-Object -ComObject WScript.Shell

$items = @(
    @{ Name = "监控屏.lnk";      Args = "`"$launch`"";              Desc = "打开笔记本状态监控屏" },
    @{ Name = "停止监控屏.lnk";  Args = "`"$launch`" --stop";       Desc = "关闭画面服务和温度服务" },
    @{ Name = "温度服务 LHM.lnk"; Args = "`"$launch`" --lhm-only";  Desc = "只重启温度服务" }
)

foreach ($it in $items) {
    $path = Join-Path $here $it.Name
    $sc = $sh.CreateShortcut($path)
    $sc.TargetPath = $Pythonw
    $sc.Arguments = $it.Args
    $sc.WorkingDirectory = $here
    $sc.Description = $it.Desc
    $sc.Save()

    # 清掉可能残留的“以管理员身份运行”标记：提权由 launch.py 自己按需触发
    $bytes = [IO.File]::ReadAllBytes($path)
    if ($bytes.Length -gt 0x15) {
        $bytes[0x15] = $bytes[0x15] -band 0xDF
        [IO.File]::WriteAllBytes($path, $bytes)
    }
    Write-Host "[ok] $($it.Name)"
}

Write-Host "[ok] shortcuts created in $here"
exit 0
