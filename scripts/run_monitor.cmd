@echo off
rem Runs one monitor cycle. Intended to be launched by the Task Scheduler
rem (via run_monitor_hidden.vbs so no console window pops up).
cd /d "%~dp0.."
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\monitor.log
python -m liquidation_tracker.cli monitor >> logs\monitor.log 2>&1
