@echo off
cd /d "%~dp0.."
start "RAGCare index" /b cmd.exe /d /c "scripts\run_ragcare_index.cmd --rebuild-index --batch-size 64 > data\ragcare\index.log 2> data\ragcare\index.err.log"
