# Снимок экрана или отдельного окна (System.Drawing.CopyFromScreen).
# Использование:
#   powershell -File shot.ps1 -Out out.png                       — весь виртуальный экран
#   powershell -File shot.ps1 -Out out.png -Title "msedge"       — окно по подстроке заголовка
#   powershell -File shot.ps1 -Out out.png -Process msedge       — главное окно процесса
#   powershell -File shot.ps1 -Out out.png -Active               — активное (foreground) окно
#   powershell -File shot.ps1 -Out out.png -X 0 -Y 0 -W 1280 -H 900   — произвольный регион
#   -Delay 500 — пауза (мс) перед снимком; -Png через System.Drawing.
param(
    [string]$Out = "$PWD\shot.png",
    [string]$Title,          # подстрока в заголовке окна (регёксп)
    [string]$Process,       # имя процесса с главным окном (msedge, powershell, ...)
    [switch]$Active,        # активное окно переднего плана
    [int]$X = -1, [int]$Y = -1, [int]$W = 0, [int]$H = 0,   # регион (W>0 включает режим региона)
    [int]$Delay = 0,
    [switch]$Client         # снимать клиентскую область окна (без рамки)
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Shot {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hWnd, out POINT lpPoint);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attr, out RECT pvAttribute, int cbAttribute);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);   // 9 = SW_RESTORE
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
}
"@

if ($Delay -gt 0) { Start-Sleep -Milliseconds $Delay }

# --- определяем прямоугольник снимка -------------------------------------------
$rect = New-Object Win32Shot+RECT
$hwnd = [IntPtr]::Zero
$source = "screen"

if ($W -gt 0) {
    # явный регион
    $rect.Left = if ($X -ge 0) { $X } else { 0 }
    $rect.Top = if ($Y -ge 0) { $Y } else { 0 }
    $rect.Right = $rect.Left + $W
    $rect.Bottom = $rect.Top + $H
    $source = "region"
} else {
    if ($Active) {
        $hwnd = [Win32Shot]::GetForegroundWindow()
        $source = "active"
    } elseif ($Title) {
        $win = Get-Process | Where-Object { $_.MainWindowTitle -and $_.MainWindowTitle -match $Title } | Select-Object -First 1
        if ($win) { $hwnd = $win.MainWindowHandle; $source = "title:'$($win.MainWindowTitle)'" }
    } elseif ($Process) {
        $win = Get-Process -Name $Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
        if ($win) { $hwnd = $win.MainWindowHandle; $source = "process:$Process" }
    }

    if ($hwnd -ne [IntPtr]::Zero) {
        # развернуть свёрнутое окно и вывести на передний план (CopyFromScreen снимает экран)
        if ([Win32Shot]::IsIconic($hwnd)) { [void][Win32Shot]::ShowWindowAsync($hwnd, 9); Start-Sleep -Milliseconds 400 }
        if ($Client) {
            $cr = New-Object Win32Shot+RECT
            [void][Win32Shot]::GetClientRect($hwnd, [ref]$cr)
            $pt = New-Object Win32Shot+POINT
            [void][Win32Shot]::ClientToScreen($hwnd, [ref]$pt)
            $rect.Left = $pt.X; $rect.Top = $pt.Y
            $rect.Right = $pt.X + $cr.Right; $rect.Bottom = $pt.Y + $cr.Bottom
        } else {
            # DWM-границы (без тени), откат на GetWindowRect
            $hr = [Win32Shot]::DwmGetWindowAttribute($hwnd, 9, [ref]$rect, [Runtime.InteropServices.Marshal]::SizeOf($rect))
            if ($hr -ne 0) { [void][Win32Shot]::GetWindowRect($hwnd, [ref]$rect) }
        }
    } else {
        # весь виртуальный экран
        $vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $rect.Left = $vs.X; $rect.Top = $vs.Y; $rect.Right = $vs.X + $vs.Width; $rect.Bottom = $vs.Y + $vs.Height
        $source = "screen"
    }
}

$w_ = $rect.Right - $rect.Left
$h_ = $rect.Bottom - $rect.Top
if ($w_ -le 0 -or $h_ -le 0) { throw "пустой прямоугольник ($w_ x $h_)" }

# окно на передний план, чтобы его не перекрывал другой снимаемый контент
if ($hwnd -ne [IntPtr]::Zero) {
    [void][Win32Shot]::SetForegroundWindow($hwnd)
    Start-Sleep -Milliseconds 250
}

$bmp = New-Object System.Drawing.Bitmap($w_, $h_)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object System.Drawing.Size($w_, $h_)))
$g.Dispose()

$outDir = Split-Path -Parent $Out
if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

[Console]::OutputEncoding = [Text.Encoding]::UTF8
Write-Output ("SHOT {0} ({1}x{2}, источник: {3})" -f $Out, $w_, $h_, $source)
