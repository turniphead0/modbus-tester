@echo off
setlocal
rem Ag klasorunden (\\sunucu\...) calistirmayi destekler: pushd gecici surucu harfi atar
pushd "%~dp0" || (echo Klasore gecilemedi: %~dp0 & pause & exit /b 1)

rem Sanal ortam yerel diskte tutulur (ag klasorunde yavas ve sorunlu olabilir)
set "VENV=%LOCALAPPDATA%\modbus-tester\venv"

rem Python bul: once py, sonra python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python bulunamadi. https://www.python.org/downloads/ adresinden Python 3.9+ kurun
  echo ve kurulumda "Add Python to PATH" secenegini isaretleyin.
  goto :end
)

if not exist "%VENV%\Scripts\python.exe" (
  echo Ilk calistirma: sanal ortam kuruluyor: %VENV%
  %PY% -m venv "%VENV%" || (echo Sanal ortam olusturulamadi. & goto :end)
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
  "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || (echo Paketler kurulamadi. Internet/proxy baglantisini kontrol edin. & rmdir /s /q "%VENV%" & goto :end)
)

"%VENV%\Scripts\python.exe" app.py

:end
popd
pause
