$ErrorActionPreference = "Stop"

# 1) Python の見つけ方：まず "python"、なければ "py -3.11"、最後に "py -3.13"
$pythonCmd = $null
$pyExe = Get-Command python -ErrorAction SilentlyContinue
if ($pyExe) {
  $pythonCmd = $pyExe.Source
} else {
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    try { & $py.Source -3.11 -V | Out-Null; $pythonCmd = "$($py.Source) -3.11" } catch { }
    if (-not $pythonCmd) { try { & $py.Source -3.13 -V | Out-Null; $pythonCmd = "$($py.Source) -3.13" } catch { } }
  }
}
if (-not $pythonCmd) {
  Write-Host "Pythonが見つかりません。https://www.python.org/ から 3.11〜3.13 をインストールしてください。"
  exit 1
}

# 2) venv が無ければ作る
if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
  & $pythonCmd -m venv .venv
}

# 3) 有効化 → 依存インストール → 起動
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
python -m app.ui.main
