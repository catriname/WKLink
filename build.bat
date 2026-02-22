@echo off
echo Building WKLink...
pyinstaller --onefile --windowed --name WKLink --icon=wklink.ico wklink.py
echo.
echo Done! EXE is in the dist\ folder.
pause
