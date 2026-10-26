@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 nodeforge.py %*
goto finished

:try_python
where python >nul 2>nul
if errorlevel 1 goto missing
python nodeforge.py %*
goto finished

:missing
echo Python was not found.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo Include Tcl/Tk and add Python to PATH, then run this file again.
pause
exit /b 1

:finished
if errorlevel 1 (
    echo.
    echo PyDrop could not start. Read the error above.
    echo Use Python 3.10 or newer with Tcl/Tk installed.
    pause
)
endlocal
