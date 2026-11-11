@echo off
rem SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
rem SPDX-License-Identifier: LGPL-3.0-or-later
rem
rem Entry point that dockur runs once, at the end of the unattended Windows installation.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo Setup failed, see C:\OEM\setup.log for details.
    pause
)
