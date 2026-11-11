$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:TEMP = Join-Path $repoRoot '.runtime/tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
Push-Location $repoRoot
try {
    $python = Join-Path $repoRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Create .venv and install .\win-desktop-control[dev] before running tests.'
    }
    & $python -B -m pytest -p no:cacheprovider -o addopts= --rootdir=. --confcutdir=. --basetemp=.runtime/pytest win-desktop-control/tests test_support/test_isolation.py -q
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed: $LASTEXITCODE" }
    & node scripts/build-client.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Client build failed' }
    & node --experimental-test-module-mocks --test test_support/client-policy.test.mjs test_support/plugin.test.mjs test_support/visual-feedback.test.mjs test_support/visual-preview.test.mjs test_support/feedback-session.test.mjs test_support/native-feedback-host.test.mjs
    if ($LASTEXITCODE -ne 0) { throw "Node tests failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}
