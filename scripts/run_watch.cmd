@echo off
rem One watch cycle: detect new auctions, build PDF reports, ping WhatsApp.
cd /d "%~dp0.."
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\watch.log
python -m liquidation_tracker.cli watch >> logs\watch.log 2>&1
