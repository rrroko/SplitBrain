@echo off
setlocal
if not exist .venv (
  py -3.11 -m venv .venv
)
call .venv\Scripts\activate

python -m pip install --upgrade pip

pip install -r requirements.txt
set PYTHONPATH=%CD%
python -m app.ui.main
