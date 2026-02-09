@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:MENU
cls
echo ============================================================
echo            FFMPEG VIDEO CUTTER - INTERACTIVE
echo ============================================================
echo.

REM Thiet lap gia tri mac dinh
set folder_input=1
set folder_output=0
set start_time=10:00
set duration=10

REM CHI NHAP THU MUC INPUT (cac gia tri khac dung mac dinh)
set /p folder_input="Input folder [%folder_input%]: "

REM Tao duong dan day du
set input_file=%folder_input%/video_cn.mp4
set output_file=%folder_output%/video_cn%folder_input%.mp4

echo.
echo === XAC NHAN ===
echo Input:  %input_file%
echo Output: %output_file%
echo Start:  %start_time%
echo Time:   %duration%s
echo.

echo.
echo ============================================================
echo Bat dau cat video...
echo ============================================================
echo.

REM Chay script Python
python cut_video.py "!input_file!" "!start_time!" !duration! "!output_file!"

echo.
echo ============================================================
echo.

REM Hoi tiep tuc hay thoat
set /p continue="Cat video khac? (Y/N) [Y]: "
if "!continue!"=="" set continue=Y

if /i "!continue!"=="Y" goto MENU
if /i "!continue!"=="YES" goto MENU

echo.
echo Tam biet!
timeout /t 2 >nul
exit