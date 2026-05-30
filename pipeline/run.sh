#!/bin/bash
# ============================================================
# run.sh — Run the detection pipeline on Linux/Mac
# ============================================================

echo "============================================"
echo " Store Intelligence Detection Pipeline"
echo "============================================"

# Install pipeline dependencies
pip install ultralytics supervision opencv-python-headless numpy requests --quiet

echo ""
echo "Starting detection pipeline..."
echo ""

python pipeline/detect.py \
    --clips-dir "./Resources/" \
    --layout "./data/store_layout.json" \
    --output "./data/events.jsonl" \
    --frame-skip 5 \
    --confidence 0.3

echo ""
echo "============================================"
echo " Pipeline complete! Events saved to data/events.jsonl"
echo "============================================"
