@echo off
title Igraonica TV Tajmer
cd /d "%~dp0"
echo Pokrecem Igraonica TV Tajmer...
start "" http://127.0.0.1:8770
where py >nul 2>nul && (py server.py) || (python server.py)
pause
