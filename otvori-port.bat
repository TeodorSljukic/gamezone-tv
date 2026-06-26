@echo off
:: Otvara Windows Firewall za GameZone (port 8770) — da se drugi PC-jevi i telefon povežu.
:: Trazi admin (klikni "Da" na UAC).
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="GameZone 8770" >nul 2>&1
netsh advfirewall firewall add rule name="GameZone 8770" dir=in action=allow protocol=TCP localport=8770
echo.
echo Port 8770 je otvoren. Sad se drugi uredjaji mogu povezati na pult.
echo.
pause
