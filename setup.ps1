param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$environmentPath = Join-Path $PSScriptRoot '.venv'
& $Python -m venv $environmentPath
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }

$environmentPython = Join-Path $environmentPath 'Scripts/python.exe'
# The SDK wheel contains deep paths; the prefix avoids Windows MAX_PATH failures.
$destination = '\\?\' + (Join-Path $environmentPath 'Lib/site-packages')
& $environmentPython -m pip install --upgrade --cache-dir (Join-Path $PSScriptRoot '.cache/pip') --target $destination -r (Join-Path $PSScriptRoot 'requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $environmentPython -c 'import temporalio; print("Temporal SDK " + temporalio.__version__)'
if ($LASTEXITCODE -ne 0) { throw 'SDK import check failed' }
