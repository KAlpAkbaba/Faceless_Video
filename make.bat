@echo off
setlocal

REM Windows equivalent of the Makefile. In cmd.exe, `make install` inside this
REM folder runs this file, so the documented commands work on Windows too.
REM In PowerShell, call it as `.\make.bat install`.

set "PY=.venv\Scripts\python.exe"
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=help"

if /I "%TARGET%"=="install" goto install
if /I "%TARGET%"=="clean"   goto clean
if /I "%TARGET%"=="help"    goto help

REM Every remaining target needs the virtualenv.
if not exist "%PY%" (
    echo ERROR: .venv not found. Run "make install" first.
    exit /b 1
)

if /I "%TARGET%"=="test"   goto test
if /I "%TARGET%"=="doctor" goto doctor
if /I "%TARGET%"=="plan"   goto plan
if /I "%TARGET%"=="probe"  goto probe
if /I "%TARGET%"=="auth"   goto auth
if /I "%TARGET%"=="dry"    goto dry
if /I "%TARGET%"=="run"    goto runall
echo Unknown target: %TARGET%
goto help

:install
if exist "%PY%" (
    echo Reusing the existing .venv
    goto deps
)

REM Windows ships Python under several names. Try each before giving up:
REM the py launcher is the most reliable, and `python` is often only the
REM Microsoft Store stub, which cannot create a virtualenv.
set "BOOTSTRAP="
py -3 --version >nul 2>nul && set "BOOTSTRAP=py -3"
if not defined BOOTSTRAP (
    python --version >nul 2>nul && set "BOOTSTRAP=python"
)
if not defined BOOTSTRAP (
    python3 --version >nul 2>nul && set "BOOTSTRAP=python3"
)
if not defined BOOTSTRAP (
    echo ERROR: no working Python was found.
    echo.
    echo Install Python 3.11+ from https://www.python.org/downloads/ and tick
    echo "Add python.exe to PATH" during setup, then reopen this window.
    echo.
    echo If Python is already installed, check what these report:
    echo     py -3 --version
    echo     where.exe python
    exit /b 1
)
echo Creating the virtual environment with: %BOOTSTRAP%
%BOOTSTRAP% -m venv .venv
if errorlevel 1 exit /b 1
if not exist "%PY%" (
    echo ERROR: .venv was not created properly.
    echo A Microsoft Store Python stub cannot build a virtualenv - install the
    echo real Python from python.org instead.
    exit /b 1
)

:deps
echo Installing dependencies...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if errorlevel 1 exit /b 1
echo.
echo Done. Next: make doctor
exit /b 0

:test
"%PY%" -m pytest tests\ -q
exit /b %errorlevel%

:doctor
"%PY%" -m pipeline.cli doctor
exit /b %errorlevel%

:plan
"%PY%" -m pipeline.cli plan
exit /b %errorlevel%

:probe
"%PY%" -m pipeline.cli probe
exit /b %errorlevel%

:auth
"%PY%" -m pipeline.cli auth
exit /b %errorlevel%

:dry
"%PY%" -m pipeline.cli run --no-upload
exit /b %errorlevel%

:runall
"%PY%" -m pipeline.cli run
exit /b %errorlevel%

:clean
if exist work rmdir /s /q work
if exist out rmdir /s /q out
if exist .pytest_cache rmdir /s /q .pytest_cache
for /d /r %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
echo Cleaned.
exit /b 0

:help
echo.
echo   make install   create .venv and install dependencies
echo   make doctor    check config, credentials and tooling
echo   make plan      print the cost estimate, spend nothing
echo   make probe     send one cheap LTX job and dump the raw response
echo   make auth      mint YouTube OAuth credentials (needs a browser)
echo   make dry       full render, nothing published
echo   make run       full pipeline and publish
echo   make test      run the test suite
echo   make clean     delete work/, out/ and caches
echo.
exit /b 0
