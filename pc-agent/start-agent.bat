@echo off
title GameZone PC Agent
cd /d "%~dp0"
where py >nul 2>nul && (py agent.py) || (python agent.py)
