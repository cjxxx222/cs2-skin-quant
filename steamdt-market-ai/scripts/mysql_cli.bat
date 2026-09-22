@echo off
chcp 65001 >nul
title MySQL 命令行

set MYSQL_HOME=D:\mysql-8.4.9-winx64

echo ============================================================
echo  MySQL 命令行客户端（数据库: steamdt_market）
echo ============================================================
echo.
echo 常用命令:
echo   SHOW TABLES;                          查看所有表
echo   DESC items;                           查看表结构
echo   SELECT * FROM items LIMIT 10;         查询数据
echo   SELECT COUNT(*) FROM prices;          统计行数
echo   exit                                  退出
echo.

"%MYSQL_HOME%\bin\mysql.exe" -u root --skip-password --default-character-set=utf8mb4 steamdt_market

pause
