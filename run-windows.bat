@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (py -m venv .venv && .venv\Scripts\pip install flask stripe qrcode pillow reportlab)
set PORT=5055
.venv\Scripts\python app.py
