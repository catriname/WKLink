@echo off
echo Building WKLink...
pyinstaller --onefile --windowed --name WKLink wklink.py

echo.
echo Building installer...
makensis installer.nsi

echo.
echo Done!  Files are in the dist\ folder:
echo   WKLink.exe         portable executable
echo   WKLink-Setup.exe   Windows installer
pause
