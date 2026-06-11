@echo off
rem One digest cycle: combined PDF of all active lots, sent by email.
cd /d "%~dp0.."
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\digest.log
python -m liquidation_tracker.cli digest >> logs\digest.log 2>&1
