@echo off
setlocal
cd /d "%~dp0.."
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set TOKENIZERS_PARALLELISM=false
".venv\Scripts\python.exe" scripts\evaluate_ragcare.py --index-only %*
