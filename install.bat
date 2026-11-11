@echo off
rem ComfyBridge (nuke-comfybridge) - by David Borrull
rem https://github.com/dborrull/nuke-comfybridge
rem MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
rem
rem One-click install on Windows: copies ComfyBridge into your .nuke folder, adds
rem it to menu.py and, if you say where ComfyUI is, copies the ComfyUI nodes.
rem Nothing is downloaded. Your own workflows and settings are kept.
setlocal EnableExtensions
title ComfyBridge - install
set "HERE=%~dp0"
set "SRC=%HERE%nuke"
set "NUKE=%USERPROFILE%\.nuke"
set "COPYOPT=/E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP"

if not exist "%SRC%\comfy_bridge.py" (
    echo Run install.bat from the unzipped nuke-comfybridge folder.
    goto :fail
)
echo.
echo  ComfyBridge - install
echo  Nuke folder: %NUKE%
echo.

if not exist "%NUKE%\" mkdir "%NUKE%" || goto :fail
copy /Y "%SRC%\comfy_bridge.py" "%NUKE%\" >nul || goto :fail
robocopy "%SRC%\comfybridge" "%NUKE%\comfybridge" %COPYOPT% >nul
if errorlevel 8 goto :fail
rem new and updated library workflows; your own ones and _disabled.json stay
robocopy "%SRC%\comfy_bridge_workflows" "%NUKE%\comfy_bridge_workflows" %COPYOPT% >nul
if errorlevel 8 goto :fail
rem icons only when missing (keep your own)
for %%I in (comfybridge.png comfybridge@2x.png) do (
    if not exist "%NUKE%\%%I" copy /Y "%SRC%\%%I" "%NUKE%\" >nul
)
echo  [ok] files copied

if exist "%NUKE%\menu.py" (
    findstr /C:"comfy_bridge.install_menu" "%NUKE%\menu.py" >nul && (
        echo  [ok] menu.py already loads ComfyBridge
        goto :comfy
    )
)
(
    echo.
    echo import comfy_bridge
    echo comfy_bridge.install_menu^(^)
) >> "%NUKE%\menu.py" || goto :fail
echo  [ok] added to menu.py

:comfy
echo.
echo  Local ComfyUI: the ComfyBridge nodes go into its custom_nodes folder.
echo  Only using Comfy Cloud? Just press Enter.
set "COMFY="
set /p "COMFY=  ComfyUI folder (e.g. C:\ComfyUI): "
if not defined COMFY goto :done
set "COMFY=%COMFY:"=%"
if "%COMFY:~-1%"=="\" set "COMFY=%COMFY:~0,-1%"
set "CN="
if exist "%COMFY%\ComfyUI\custom_nodes\" set "CN=%COMFY%\ComfyUI\custom_nodes"
if not defined CN if exist "%COMFY%\custom_nodes\" set "CN=%COMFY%\custom_nodes"
if not defined CN if /I "%COMFY:~-12%"=="custom_nodes" if exist "%COMFY%\" set "CN=%COMFY%"
if not defined CN (
    echo  [!] No custom_nodes folder in "%COMFY%". Skipped: copy
    echo      comfyui\ComfyUI-ComfyBridge into ComfyUI\custom_nodes by hand.
    goto :done
)
robocopy "%HERE%comfyui\ComfyUI-ComfyBridge" "%CN%\ComfyUI-ComfyBridge" %COPYOPT% >nul
if errorlevel 8 goto :fail
echo  [ok] ComfyUI nodes copied to %CN%\ComfyUI-ComfyBridge
echo       Restart ComfyUI.

:done
echo.
echo  Done. Restart Nuke: Nodes ^> ComfyUI ^> ComfyBridge.
echo.
pause
exit /b 0

:fail
echo.
echo  [x] Install failed (see the message above). Nothing else was changed.
echo.
pause
exit /b 1
