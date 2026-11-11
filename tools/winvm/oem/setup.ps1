# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Install Git, uv and an OpenSSH server in a fresh Windows VM, for testing StepUp over SSH.
# The setup is complete when C:\OEM\setup-done.txt exists.
# Windows LTSC has no winget, so all tools are downloaded from their GitHub releases.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Start-Transcript -Path 'C:\OEM\setup.log' -Append | Out-Null

$downloads = 'C:\OEM\downloads'
New-Item -ItemType Directory -Force -Path $downloads | Out-Null

function Invoke-Checked {
    # Run a console program and throw if it fails.
    # Progress output on stderr must not count as an error, hence the local preference.
    param([string]$FilePath, [string[]]$ArgumentList)
    $ErrorActionPreference = 'Continue'
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) { throw "$FilePath exited with code $LASTEXITCODE" }
}

function Install-Package {
    # Run a GUI installer and wait for it, because the call operator would not wait.
    param([string]$FilePath, [string[]]$ArgumentList)
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru
    # 3010 means success with a reboot required.
    if ($process.ExitCode -notin 0, 3010) {
        throw "$FilePath exited with code $($process.ExitCode)"
    }
}

function Get-File {
    param([string]$Url, [string]$Name)
    $path = Join-Path $downloads $Name
    Write-Host "Downloading $Url"
    Invoke-Checked 'curl.exe' @(
        '--fail', '--location', '--retry', '5', '--retry-all-errors', '--output', $path, $Url
    )
    return $path
}

function Get-LatestAssetUrl {
    param([string]$Repo, [string]$Pattern)
    $json = Get-File "https://api.github.com/repos/$Repo/releases/latest" 'release.json'
    $release = Get-Content -Raw -Encoding UTF8 -Path $json | ConvertFrom-Json
    $asset = $release.assets | Where-Object { $_.name -match $Pattern } | Select-Object -First 1
    if (-not $asset) { throw "No asset matches $Pattern in the latest release of $Repo" }
    return $asset.browser_download_url
}

function Add-MachinePath {
    param([string]$Directory)
    $path = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    if (($path -split ';') -notcontains $Directory) {
        [Environment]::SetEnvironmentVariable('Path', "$path;$Directory", 'Machine')
    }
}

Write-Host '==> Installing Git for Windows'
$url = Get-LatestAssetUrl 'git-for-windows/git' '^Git-[0-9.]+-64-bit\.exe$'
$installer = Get-File $url 'git-setup.exe'
Install-Package $installer @('/VERYSILENT', '/NORESTART', '/NOCANCEL', '/SP-', '/SUPPRESSMSGBOXES')
$git = 'C:\Program Files\Git\cmd\git.exe'
# Check out files byte for byte as on Linux,
# so that test differences come from StepUp and not from line-ending conversion.
Invoke-Checked $git @('config', '--system', 'core.autocrlf', 'false')
# The repository contains symbolic links,
# which the VM user may create because it is an administrator without UAC.
Invoke-Checked $git @('config', '--system', 'core.symlinks', 'true')

Write-Host '==> Installing uv'
$uvDir = 'C:\Program Files\uv'
$url = 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
$zip = Get-File $url 'uv.zip'
Expand-Archive -Path $zip -DestinationPath $uvDir -Force
Add-MachinePath $uvDir

Write-Host '==> Excluding the user profile from real-time virus scanning'
# Scanning every file that Python, uv and pytest touch slows down the tests a lot.
try {
    Add-MpPreference -ExclusionPath $env:USERPROFILE, $uvDir
} catch {
    Write-Warning "Could not add Defender exclusions: $_"
}

Write-Host '==> Installing the OpenSSH server'
$url = Get-LatestAssetUrl 'PowerShell/Win32-OpenSSH' '^OpenSSH-Win64-v[0-9.]+\.msi$'
$msi = Get-File $url 'openssh.msi'
Install-Package 'msiexec.exe' @('/i', $msi, '/qn', '/norestart')
$key = 'HKLM:\SOFTWARE\OpenSSH'
if (-not (Test-Path $key)) { New-Item -Path $key | Out-Null }
$powershell = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
New-ItemProperty -Path $key -Name DefaultShell -Value $powershell -PropertyType String -Force |
    Out-Null
if (-not (Get-NetFirewallRule -Name 'StepUp-sshd' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'StepUp-sshd' -DisplayName 'OpenSSH Server (StepUp test VM)' `
        -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Any | Out-Null
}
Set-Service -Name sshd -StartupType Automatic
Start-Service -Name sshd
# Administrators authenticate with this file instead of the one in their profile.
$authorizedKeys = 'C:\ProgramData\ssh\administrators_authorized_keys'
New-Item -ItemType Directory -Force -Path (Split-Path $authorizedKeys) | Out-Null
Copy-Item -Path 'C:\OEM\authorized_keys' -Destination $authorizedKeys -Force
# sshd ignores the file unless only Administrators and SYSTEM can access it.
Invoke-Checked 'icacls.exe' @(
    $authorizedKeys, '/inheritance:r', '/grant', '*S-1-5-32-544:F', '/grant', '*S-1-5-18:F'
)

Set-Content -Path 'C:\OEM\setup-done.txt' -Value (Get-Date -Format o)
Write-Host '==> Setup finished, the VM is ready for testing'
Stop-Transcript | Out-Null
