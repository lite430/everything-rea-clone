@echo off
chcp 65001 >nul
title Everything 克隆（REA 逆向复刻版）
cd /d "%~dp0"
echo ================================================
echo  Everything 克隆启动中：正在扫描 NTFS MFT 建立索引...
echo  （需要管理员权限；索引约 1 分钟，之后实时更新）
echo ================================================
python everything.py --server 8888 --etp 8899 --ipc --file-list analysis\demo-filelist.txt
pause
