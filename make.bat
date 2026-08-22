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
if /I "%TARGET%"=="storyboard" goto storyboard
if /I "%TARGET%"=="dry"    goto dry
if /I "%TARGET%"=="run"    goto runall
echo Unknown target: %TARGET%
goto help

:install
if exist "%PY%" (
    "%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>nul
    if errorlevel 1 goto stalevenv
    echo Reusing the existing .venv
    goto deps
)

REM Windows ships Python under several names, and a fresh install does not
REM always become the launcher's default: PY_PYTHON or a py.ini can pin `py -3`
REM to an old version. Ask for explicit versions first - those ignore the
REM default - and only then fall back to the generic names. `python` alone is
REM often just the Microsoft Store stub, which cannot create a virtualenv.
set "BOOTSTRAP="

REM An explicit override always wins:  set PYTHON_EXE=C:\path\to\python.exe
if defined PYTHON_EXE (
    "%PYTHON_EXE%" --version >nul 2>nul && set "BOOTSTRAP="%PYTHON_EXE%""
    if not defined BOOTSTRAP echo WARNING: PYTHON_EXE is set but "%PYTHON_EXE%" did not run.
)

for %%v in (3.14 3.13 3.12 3.11) do (
    if not defined BOOTSTRAP (
        py -%%v --version >nul 2>nul && set "BOOTSTRAP=py -%%v"
    )
)
if not defined BOOTSTRAP ( py -3 --version >nul 2>nul && set "BOOTSTRAP=py -3" )
if not defined BOOTSTRAP ( python --version >nul 2>nul && set "BOOTSTRAP=python" )
if not defined BOOTSTRAP ( python3 --version >nul 2>nul && set "BOOTSTRAP=python3" )

if not defined BOOTSTRAP (
    echo ERROR: no working Python was found.
    echo.
    echo Install Python 3.11+ from https://www.python.org/downloads/ and tick
    echo "Add python.exe to PATH" during setup, then reopen this window.
    exit /b 1
)
REM Refuse before building anything: a venv made by an old interpreter fails
REM later with a confusing "No module named 'zoneinfo'" instead of a clear message.
%BOOTSTRAP% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>nul
if errorlevel 1 (
    echo ERROR: %BOOTSTRAP% is older than Python 3.11, which this pipeline requires.
    for /f "tokens=*" %%v in ('%BOOTSTRAP% --version 2^>^&1') do echo Found: %%v
    echo.
    echo If you just installed a newer Python, it is on this machine but not the
    echo launcher's default. List every interpreter with its full path:
    echo     py -0p
    echo Then point this script straight at the new one, for example:
    echo     set PYTHON_EXE=%%LOCALAPPDATA%%\Programs\Python\Python313\python.exe
    echo     make install
    echo.
    echo Otherwise install Python 3.11+ from https://www.python.org/downloads/,
    echo tick "Add python.exe to PATH", and reopen this window.
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

:stalevenv
echo ERROR: the existing .venv was built with a Python older than 3.11.
"%PY%" --version
echo.
echo Delete it and rebuild against a current Python:
echo     rmdir /s /q .venv
echo     make install
echo.
echo If "py -3 --version" also reports an old version, install Python 3.11+
echo from https://www.python.org/downloads/ first.
exit /b 1

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

:storyboard
"%PY%" -m pipeline.cli storyboard
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
echo   make storyboard plan one episode and cost it, generating no video
echo   make dry       full render, nothing published
echo   make run       full pipeline and publish
echo   make test      run the test suite
echo   make clean     delete work/, out/ and caches
echo.
exit /b 0
