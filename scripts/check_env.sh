#!/bin/bash
cd /home/a/RAG_drift
echo "=== DOCKER ===" 
docker compose ps 2>&1
echo "=== END DOCKER ==="
echo "=== GROUPS ===" 
groups
echo "=== VENV ==="
ls .venv/bin/python 2>&1
echo "=== DONE ==="
