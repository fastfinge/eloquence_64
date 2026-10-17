@echo off
rem Builds the Eloquence Contours DLL from the eloquence-contours submodule and
rem copies it, its Python adapter and its licence into addon\synthDrivers\contours.
rem
rem Only the 64-bit DLL is built.  Contours are applied once, in the Audio
rem Playback Pipeline inside NVDA's own process, to audio from either backend; the
rem contour DLL has to match the calling process, not the engine, so the 32-bit
rem Eloquence Host Process never loads it.
rem
rem   build_contours.cmd           build the commit the submodule is pinned to
rem   build_contours.cmd --latest  move the submodule to upstream master first
setlocal
set hereOrig=%~dp0
set here=%hereOrig%
if #%hereOrig:~-1%# == #\# set here=%hereOrig:~0,-1%
set src=%here%\eloquence-contours
set dest=%here%\addon\synthDrivers\contours

if /i "%~1" == "--latest" (
	git -C "%here%" submodule update --init --remote eloquence-contours
	if ERRORLEVEL 1 exit /b %ERRORLEVEL%
) else if not exist "%src%\Cargo.toml" (
	rem Only initialise: an update on an already checked-out submodule would reset
	rem a commit a developer is trying out back to the pinned one.
	git -C "%here%" submodule update --init eloquence-contours
	if ERRORLEVEL 1 exit /b %ERRORLEVEL%
)

rem Static CRT, matching upstream's own build.cmd, so no runtime redistributable
rem has to be present on a user's machine.
set "RUSTFLAGS=-C target-feature=+crt-static"
cargo build --release --target x86_64-pc-windows-msvc --manifest-path "%src%\Cargo.toml" --target-dir "%here%\build\contours"
if ERRORLEVEL 1 exit /b %ERRORLEVEL%

if exist "%dest%" rd /S /Q "%dest%"
mkdir "%dest%"
copy /Y "%here%\build\contours\x86_64-pc-windows-msvc\release\eloquence_contours.dll" "%dest%\" >nul || exit /b 1
copy /Y "%src%\python\eloquence_contours.py" "%dest%\" >nul || exit /b 1
copy /Y "%src%\LICENSE" "%dest%\" >nul || exit /b 1
rem Nothing in the add-on pins a contour version at run time, so record which
rem commit this build carries, as openevv-version.txt does for the engine.
for /f "usebackq delims=" %%V in (`git -C "%src%" rev-parse --short^=8 HEAD`) do set contoursCommit=%%V
for /f "usebackq tokens=2 delims== " %%V in (`findstr /b /c:"version" "%src%\Cargo.toml"`) do set contoursVersion=%%~V
>"%dest%\contours-version.txt" echo %contoursVersion% (%contoursCommit%)
echo Eloquence Contours %contoursVersion% (%contoursCommit%) installed in %dest%.
