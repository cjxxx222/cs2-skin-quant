@echo off
chcp 65001 >nul
title 启动 MySQL 服务

set MYSQL_HOME=D:\mysql-8.4.9-winx64

echo ============================================================
echo  启动 MySQL 8.4.9
echo ============================================================
echo.

if not exist "%MYSQL_HOME%\bin\mysqld.exe" (
    echo [错误] 找不到 %MYSQL_HOME%\bin\mysqld.exe
    echo        请确认 MySQL 安装目录是否正确。
    pause
    exit /b 1
)

echo 正在启动... 启动后本窗口会保持运行（关闭窗口即停止 MySQL）
echo 如需连接数据库，另开一个终端运行 scripts\mysql_cli.bat
echo.

"%MYSQL_HOME%\bin\mysqld.exe" --defaults-file="%MYSQL_HOME%\my.ini" --console

pause
