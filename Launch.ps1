$ErrorActionPreference = 'Stop'
$pythonWindowExe = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowExe)) {
    throw 'Project environment missing. Run: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt'
}
$guiPath = Join-Path $PSScriptRoot 'gui.py'
Start-Process -FilePath $pythonWindowExe -ArgumentList @('-X', 'utf8', ('"' + $guiPath + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
