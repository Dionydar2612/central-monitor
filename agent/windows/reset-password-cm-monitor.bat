@echo off
REM ======================================================================
REM  reset-password-cm-monitor.bat - Central Monitor (agentless Windows monitoring)
REM  Re-run setup and give the cm-monitor user a NEW password (also re-signs the module).
REM  Double-click. Then put the new password in central: Settings > this machine > Connection.
REM ======================================================================
call "%~dp0setup-cm-monitor.bat" resetpw
