@echo off
chcp 65001 >nul
echo 即将打开 Edge 扩展管理页。
echo 1. 打开右上角「开发人员模式」
echo 2. 点「加载解压缩的扩展」
echo 3. 选择本文件夹（%CD%）
start "" msedge edge://extensions
start "" explorer "%~dp0"
