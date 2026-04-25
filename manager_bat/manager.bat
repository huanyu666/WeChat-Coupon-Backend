@echo off
set SERVICE_NAME=WXAPP
set LOG_PATH=C:\Users\Administrator\Desktop\wx_server\log\log.txt

if "%1"=="start" (
    echo 启动服务 %SERVICE_NAME%...
    nssm start %SERVICE_NAME%
) else if "%1"=="stop" (
    echo 停止服务 %SERVICE_NAME%...
    nssm stop %SERVICE_NAME%
) else if "%1"=="restart" (
    echo 重启服务 %SERVICE_NAME%...
    nssm stop %SERVICE_NAME% >nul 2>&1
    timeout /t 3 /nobreak >nul
    nssm start %SERVICE_NAME%
) else if "%1"=="status" (
    echo 服务 %SERVICE_NAME% 状态:
    nssm status %SERVICE_NAME%
) else if "%1"=="logs" (
    call :TAIL_LOG
) else (
    echo.
    echo 用法: %0 [start^|stop^|restart^|status^|logs]
    echo.
    echo   start    - 启动服务
    echo   stop     - 停止服务
    echo   restart  - 重启服务
    echo   status   - 查看服务状态
    echo   logs     - 实时查看日志 (%LOG_PATH%)
    echo.
    exit /b 1
)

exit /b 0


::----------------------------------------
:: 子程序：实时输出日志新增内容（类似 tail -f）
::----------------------------------------
:TAIL_LOG
echo.
echo 正在监控日志文件: %LOG_PATH%
echo (按 Ctrl+C 退出监控)
echo.

:: 检查日志文件是否存在
if not exist "%LOG_PATH%" (
    echo 错误: 日志文件不存在: %LOG_PATH%
    echo 请确认服务已运行且日志路径正确。
    pause
    exit /b 1
)

:: 初始读取整个文件
echo [启动时日志内容]
type "%LOG_PATH%"
echo.

:: 获取初始文件大小
for %%A in ("%LOG_PATH%") do set /a "LAST_SIZE=%%~zA"

:TAIL_LOOP
:: 延迟1秒
timeout /t 1 /nobreak >nul

:: 获取当前文件大小
for %%A in ("%LOG_PATH%") do set /a "CURRENT_SIZE=%%~zA"

:: 如果文件变大，输出新增内容
if %CURRENT_SIZE% GTR %LAST_SIZE% (
    REM 计算跳过的行数（保守估计，按每行平均64字符估算）
    set /a "LINES_TO_SKIP=%LAST_SIZE% / 64"

    REM 使用 more 命令跳过前面的内容，只显示新增部分
    more +%"LINES_TO_SKIP%" "%LOG_PATH%" 2>nul

    REM 更新最后大小
    set /a "LAST_SIZE=%CURRENT_SIZE%"
)

goto :TAIL_LOOP