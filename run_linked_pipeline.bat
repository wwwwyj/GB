@echo off
setlocal EnableDelayedExpansion

REM Link two environments:
REM 1. Docker container paddleocr3 runs GPU PaddleOCR 3.x and writes output\ocr_manifest.json.
REM 2. Windows Pytorch reads that manifest and runs model/RAG/Excel steps.
REM No Chinese path is stored in this .bat file to avoid Windows cmd encoding issues.

set "CONTAINER_NAME=paddleocr3"
set "CONTAINER_PROJECT=/workspace/standard_extraction"
set "OUTPUT_DIR=output"

REM Parse --output-dir if supplied.
set "PREV_ARG="
for %%A in (%*) do (
    if /I "!PREV_ARG!"=="--output-dir" (
        set "OUTPUT_DIR=%%~A"
    )
    set "PREV_ARG=%%~A"
)

docker start %CONTAINER_NAME% >nul
if errorlevel 1 (
    echo Failed to start Docker container %CONTAINER_NAME%.
    exit /b 1
)

REM Create an ASCII symlink inside the container: /workspace/standard_extraction -> the project folder containing main.py and config.yaml.
REM Do not use Python != here because cmd delayed expansion treats ! specially.
docker exec -w /workspace %CONTAINER_NAME% python -c "from pathlib import Path; root=Path('/workspace'); target=root/'standard_extraction'; candidates=[p for p in root.iterdir() if p.is_dir() and p.name not in ('standard_extraction',) and (p/'main.py').exists() and (p/'config.yaml').exists()]; target.exists() or target.is_symlink() or target.symlink_to(candidates[0], target_is_directory=True); print(target.resolve())"
if errorlevel 1 (
    echo Failed to create or resolve /workspace/standard_extraction inside Docker container.
    exit /b 1
)

REM Run OCR inside the Linux container. All user arguments are passed through.
docker exec -w "%CONTAINER_PROJECT%" %CONTAINER_NAME% python scripts/ocr_only.py --config config.yaml %*
if errorlevel 1 (
    echo OCR stage failed inside Docker container.
    exit /b 1
)

REM Prefer the known Pytorch environment path. Do not use base conda by accident.
if exist "C:\ProgramData\anaconda3\envs\Pytorch\python.exe" (
    set "PYTHON_EXE=C:\ProgramData\anaconda3\envs\Pytorch\python.exe"
)

REM Fall back to the currently activated conda environment.
if not defined PYTHON_EXE (
    if defined CONDA_PREFIX (
        if exist "%CONDA_PREFIX%\python.exe" (
            set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
        )
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

"%PYTHON_EXE%" main.py --config config.yaml --raw-document-manifest "%OUTPUT_DIR%\ocr_manifest.json"
exit /b %ERRORLEVEL%
