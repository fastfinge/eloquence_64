@echo off
py -3.13-32 -m PyInstaller --onefile --noconsole --name eloquence_host32 host_eloquence32.py
python build.py