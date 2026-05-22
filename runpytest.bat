@echo off
set hereOrig=%~dp0
set here=%hereOrig%
if #%hereOrig:~-1%# == #\# set here=%hereOrig:~0,-1%
call uv run --group unit-tests --directory "%here%" pytest %*
if ERRORLEVEL 5 exit /b 0
if ERRORLEVEL 1 exit /b %ERRORLEVEL%
