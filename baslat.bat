@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Ilk calistirma: sanal ortam kuruluyor...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python app.py
pause
