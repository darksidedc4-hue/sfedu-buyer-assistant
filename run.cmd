@echo off
rem Zapusk po kontraktu v Windows: run.cmd --catalog C:\put\catalog.json ^< request.json ^> response.json
setlocal
where py >nul 2>nul && (py -3 "%~dp0assistant\main.py" %*) || (python "%~dp0assistant\main.py" %*)
exit /b %ERRORLEVEL%
