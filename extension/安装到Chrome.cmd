@echo off
chcp 65001 >nul
echo 即将打开 Chrome 扩展管理页。
echo 1. 打开右上角「开发者模式」
echo 2. 点「加载已解压的扩展程序」
echo 3. 选择本文件夹（%CD%）
start "" chrome chrome://extensions
start "" explorer "%~dp0"
