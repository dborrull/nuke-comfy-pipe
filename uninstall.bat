@echo off
rem ComfyBridge (nuke-comfybridge) - by David Borrull
rem https://github.com/dborrull/nuke-comfybridge
rem MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
rem
rem Removes ComfyBridge from your .nuke folder and menu.py. The workflow library
rem (which may hold your own workflows) is kept unless you say so, and so are the
rem settings and your renders.
setlocal EnableExtensions
title ComfyBridge - uninstall
set "NUKE=%USERPROFILE%\.nuke"
echo.
echo  ComfyBridge - uninstall
echo  Nuke folder: %NUKE%
echo.

if exist "%NUKE%\comfy_bridge.py" del /Q "%NUKE%\comfy_bridge.py"
if exist "%NUKE%\comfybridge\" rmdir /S /Q "%NUKE%\comfybridge"
for %%I in (comfybridge.png comfybridge@2x.png) do if exist "%NUKE%\%%I" del /Q "%NUKE%\%%I"
echo  [ok] ComfyBridge files removed

if exist "%NUKE%\menu.py" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = $env:NUKE + '\menu.py'; $l = @([IO.File]::ReadAllLines($p) | Where-Object { $_ -notmatch '^\s*(import comfy_bridge|comfy_bridge\.install_menu\(\))\s*$' }); [IO.File]::WriteAllLines($p, [string[]]$l)" <nul && echo  [ok] removed from menu.py
)

if exist "%NUKE%\comfy_bridge_workflows\" (
    echo.
    set "ANS="
    set /p "ANS=  Also delete the workflow library (comfy_bridge_workflows, with your own workflows)? [y/N] "
    call :maybe_delete_library
)

echo.
echo  ComfyUI nodes: press Enter to keep them, or give the ComfyUI folder to remove them.
set "COMFY="
set /p "COMFY=  ComfyUI folder (e.g. C:\ComfyUI): "
if not defined COMFY goto :done
set "COMFY=%COMFY:"=%"
if "%COMFY:~-1%"=="\" set "COMFY=%COMFY:~0,-1%"
set "CN="
if exist "%COMFY%\ComfyUI\custom_nodes\ComfyUI-ComfyBridge\" set "CN=%COMFY%\ComfyUI\custom_nodes\ComfyUI-ComfyBridge"
if not defined CN if exist "%COMFY%\custom_nodes\ComfyUI-ComfyBridge\" set "CN=%COMFY%\custom_nodes\ComfyUI-ComfyBridge"
if not defined CN if exist "%COMFY%\ComfyUI-ComfyBridge\" set "CN=%COMFY%\ComfyUI-ComfyBridge"
if not defined CN (
    echo  [!] ComfyUI-ComfyBridge not found in "%COMFY%". Nothing removed there.
    goto :done
)
rmdir /S /Q "%CN%" && echo  [ok] removed %CN%. Restart ComfyUI.

:done
echo.
echo  Done. Restart Nuke. Kept: comfy_bridge_settings.json and your renders.
echo.
pause
exit /b 0

:maybe_delete_library
if /I "%ANS%"=="y" (
    rmdir /S /Q "%NUKE%\comfy_bridge_workflows" && echo  [ok] workflow library deleted
) else (
    echo  [ok] workflow library kept
)
exit /b 0
