@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d D:\Code\Rust\SC2Diff
copy /y reference\StormLib\build\Release\StormLib.lib reference\StormLib\build\Release\StormLibRAD.lib >nul
cl /nologo /EHsc /O2 /MD /I reference\StormLib\src tools\stormlib_verify.cpp /link /LIBPATH:reference\StormLib\build\Release user32.lib /OUT:tools\stormlib_verify.exe
echo EXITCODE=%ERRORLEVEL%
