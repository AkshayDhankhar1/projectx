@echo off
REM ============================================================
REM run.bat — Run the detection pipeline on Windows
REM ============================================================
REM This script processes all CCTV clips and generates events.
REM 
REM Usage: pipeline\run.bat
REM ============================================================

echo ============================================
echo  Store Intelligence Detection Pipeline
echo ============================================
echo.

REM Install pipeline dependencies if not already installed
pip install ultralytics supervision opencv-python-headless numpy requests --quiet

echo.
echo Starting detection pipeline...
echo.

python pipeline/detect.py ^
    --clips-dir "./Resources/" ^
    --layout "./data/store_layout.json" ^
    --output "./data/events.jsonl" ^
    --frame-skip 5 ^
    --confidence 0.3

echo.
echo ============================================
echo  Pipeline complete! Events saved to data/events.jsonl
echo ============================================
echo.
echo Next steps:
echo   1. Start the API:  uvicorn app.main:app --reload
echo   2. Ingest events:  python scripts/ingest_events.py
echo   3. View dashboard: open http://localhost:3000
