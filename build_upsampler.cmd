@echo off

set PATH=C:\msys64\mingw64\bin;%PATH%
if not exist C:\msys64\mingw64\bin\gcc.exe (
    echo MSYS2 MinGW64 not found!
    pause
    exit /b
)
echo Building upsampler.dll...

gcc -shared -static-libgcc -o upsampler.dll upsampler.c                                      

if %errorlevel% neq 0 (
    echo Build failed!
    exit /b %errorlevel%
)

echo Copying DLL to addon folder...

copy /Y upsampler.dll addon\synthDrivers\upsampler.dll

echo Done!