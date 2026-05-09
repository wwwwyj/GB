@echo off
setlocal

REM Prefer the Python from the currently activated conda environment.
if defined CONDA_PREFIX (
    if exist "%CONDA_PREFIX%\python.exe" (
        set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
    )
)

REM Fall back to the known Pytorch environment path.
if not defined PYTHON_EXE (
    if exist "C:\ProgramData\anaconda3\envs\Pytorch\python.exe" (
        set "PYTHON_EXE=C:\ProgramData\anaconda3\envs\Pytorch\python.exe"
    )
)

REM Last fallback: use python from PATH.
if not defined PYTHON_EXE (
    python --version >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. Activate the Pytorch environment first.
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" main.py --config config.yaml %*
exit /b %ERRORLEVEL%
