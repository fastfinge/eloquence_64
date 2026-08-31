@echo off
set hereOrig=%~dp0
set here=%hereOrig%
if #%hereOrig:~-1%# == #\# set here=%hereOrig:~0,-1%
if "%PYTHON32%" == "" (
	for /f "usebackq delims=" %%P in (`py -3.13-32 -c "import sys; print(sys.executable)"`) do set PYTHON32=%%P
)
set UV_PROJECT_ENVIRONMENT=%here%\.venv32
rem --onedir, not --onefile: a onefile build re-extracts its whole archive to
rem %TEMP% on every launch, which cost 1.1-3.5s before the Eloquence Host Process
rem even opened the Host Channel, and is also what most antivirus heuristics
rem flag.  A onedir tree starts from disk with no extraction step.
rem --noconfirm because --onedir refuses to overwrite a populated dist directory
rem interactively, which would stall every rebuild after the first.
call uv run --group host-build --python "%PYTHON32%" --directory "%here%" PyInstaller --onedir --noconfirm --noconsole --name eloquence_host32 host_eloquence32.py
if ERRORLEVEL 1 exit /b %ERRORLEVEL%
rem Replace the previous tree outright so files dropped between builds do not
rem linger in the packaged add-on.
if exist "%here%\addon\synthDrivers\eloquence_host32" rd /S /Q "%here%\addon\synthDrivers\eloquence_host32"
if exist "%here%\addon\synthDrivers\eloquence_host32.exe" del /Q "%here%\addon\synthDrivers\eloquence_host32.exe"
xcopy /E /I /Y "%here%\dist\eloquence_host32" "%here%\addon\synthDrivers\eloquence_host32"
if ERRORLEVEL 1 exit /b %ERRORLEVEL%
