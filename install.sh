#!/bin/sh
cd /opt/data/model-router
/opt/data/model-router/venv/bin/python3 -m pip install fastapi uvicorn httpx loguru
echo "DONE"
