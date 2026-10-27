@echo off
chcp 65001 >nul
title Everything 克隆 · 桌面客户端
cd /d "%~dp0client"
"node_modules\.bin\electron.cmd" .
