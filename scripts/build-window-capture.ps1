param()
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/Include'
$sdk = Get-ChildItem -LiteralPath $sdkRoot -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'cppwinrt/winrt/Windows.Graphics.Capture.h') } |
    Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
if (-not $sdk) { throw 'Install the Windows SDK with the C++/WinRT headers.' }
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vs) { throw 'Install Visual Studio with the MSVC C++ tools.' }
    Import-Module (Join-Path $vs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
    Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments "-arch=x64 -host_arch=x64 -winsdk=$($sdk.Name)"
}
$staging = Join-Path $root '.build/window-capture'
$package = Join-Path $root 'win-desktop-control/src/wdc'
New-Item -ItemType Directory -Path $staging -Force | Out-Null
$cppwinrt = Join-Path $sdk.FullName 'cppwinrt'
$arguments = @('/nologo','/O2','/std:c++20','/EHsc','/MT','/W4',"/I$cppwinrt","/Fo$staging/wgc_capture.obj",
    "/Fe$package/wgc_capture.exe",(Join-Path $root 'win-desktop-control/native/wgc_capture.cpp'),
    '/link','d3d11.lib','dwmapi.lib','windowsapp.lib','user32.lib')
& cl.exe @arguments
if ($LASTEXITCODE -ne 0) { throw "Windows Graphics Capture build failed ($LASTEXITCODE)." }
