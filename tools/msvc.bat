@echo off
rem Run a command inside the MSVC build-tools environment.
rem   usage:  tools\msvc.bat <command> [args...]
rem
rem Where the build tools live is a property of the machine, not of the
rem project, so the path is not written down here.  It is taken from:
rem
rem   1. the environment variable AURALIS_VCVARS64, if it is set;
rem   2. tools\msvc.local.bat, a file of the machine that is not committed;
rem   3. vcvars64.bat on the PATH.
if defined AURALIS_VCVARS64 (
  call "%AURALIS_VCVARS64%" >nul 2>&1
) else if exist "%~dp0msvc.local.bat" (
  call "%~dp0msvc.local.bat" >nul 2>&1
) else (
  call vcvars64.bat >nul 2>&1
)

if errorlevel 1 (
  echo [msvc] the compiler environment could not be set up
  echo [msvc]   set AURALIS_VCVARS64 to the vcvars64.bat of the build tools
  exit /b 1
)
%*
exit /b %errorlevel%
