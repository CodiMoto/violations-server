# Violations Server - install or update on a manager's computer.
#
# Installs everything it needs that isn't there yet: Python, the Python libraries,
# SumatraPDF (silent printing) and Tailscale (the phone address) - then signs this
# computer in to Tailscale and turns on Funnel.
#
#   Right-click this file > "Run with PowerShell"   (or: powershell -ExecutionPolicy Bypass -File install.ps1)
#
# Safe to run again at any time (after downloading a new version, too): it keeps
# this computer's settings, login and data, and only fills in what's missing.
#
#   -RestartOnly   just restart the phone server (used by Violations Settings)

param([switch]$RestartOnly, [string]$User = "$env:USERDOMAIN\$env:USERNAME")
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$task = 'Violations Server'
$printTask = 'Violations Print'
$venvPy = Join-Path $dir 'venv\Scripts\python.exe'
$venvPyw = Join-Path $dir 'venv\Scripts\pythonw.exe'

function Say($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

# Settings files are UTF-8 without a byte-order mark (Python reads them).
function Write-Json($obj, $path) {
    [IO.File]::WriteAllText($path, ($obj | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding $false))
}
function Read-Json($path) { Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json }

function Restart-Server {
    # The task runs venv\Scripts\pythonw.exe, which starts the real Python as a
    # child process - so end the task AND whatever is holding the phone port.
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    # (Stop-Process, not taskkill: under 'Stop', taskkill's "not found" when the task
    # already ended it aborted this script and left the server off.)
    Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    Start-ScheduledTask -TaskName $task
    Start-Sleep 4
    if (Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue) {
        Write-Host 'Phone server is running.' -ForegroundColor Green
    } else {
        Write-Host 'Phone server did not start - see data\violations\server.log' -ForegroundColor Yellow
    }
}

if ($RestartOnly) { Restart-Server; exit 0 }

# Starting the phone server with Windows (before anyone signs in) needs administrator
# rights to set up, so ask Windows for them. -User keeps everything for the person who
# ran this, even if Windows asks for a different administrator's password.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -Wait -ArgumentList `
        "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -User `"$User`""
    exit 0
}

# This runs in its own window: on any error, say what happened instead of vanishing.
trap {
    Write-Host "`nSomething went wrong: $_" -ForegroundColor Red
    Write-Host 'Fix that (often: check the internet connection) and run install.ps1 again - it keeps what is already done.'
    Read-Host 'Press Enter to close'
    exit 1
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'     # Windows PowerShell downloads crawl with the progress bar on

function Get-File($url, $name) {
    $out = Join-Path $env:TEMP $name
    Write-Host "Downloading $url ..."
    Invoke-WebRequest $url -OutFile $out -UseBasicParsing
    return $out
}

Write-Host 'Violations Server setup' -ForegroundColor White
Write-Host "Folder: $dir"

# 1. Python ---------------------------------------------------------------------------
#    Installed for all users (C:\Program Files), because the phone server runs as a
#    scheduled task that must be able to reach it whoever is signed in.
Say 'Python'
function Find-Python {
    Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -match '^Python3(\d+)$' -and [int]$Matches[1] -ge 10 } |
        Sort-Object { [int]($_.Directory.Name -replace 'Python3', '') } -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
$py = Find-Python
if (-not $py -and -not (Test-Path $venvPy)) {
    Write-Host 'Python is not installed for all users - installing Python 3.12 (a few minutes)...'
    $exe = Get-File 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' 'python-3.12.10-amd64.exe'
    $p = Start-Process $exe -Wait -PassThru -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_test=0'
    Remove-Item $exe -ErrorAction SilentlyContinue
    $py = Find-Python
    if (-not $py) {
        Write-Host "Python didn't install (code $($p.ExitCode)). Install Python 3.12 from https://www.python.org/downloads/ -" -ForegroundColor Yellow
        Write-Host '"Customize installation" > tick "Install Python for all users" - then run install.ps1 again.' -ForegroundColor Yellow
        Read-Host 'Press Enter to close'
        exit 1
    }
}
if ($py) { Write-Host "Using $py" -ForegroundColor Green } else { Write-Host 'Using the Python already set up in this folder.' -ForegroundColor Green }

# 2. Its own copy of the libraries ---------------------------------------------------------
Say 'Libraries'
if (-not (Test-Path $venvPy)) {
    & $py -m venv (Join-Path $dir 'venv')
}
& $venvPy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $venvPy -m pip install --quiet --disable-pip-version-check -r (Join-Path $dir 'requirements.txt')
Write-Host 'Done.' -ForegroundColor Green

# 3. Silent printing helper (SumatraPDF, open source) -----------------------------------------
Say 'Printing helper'
$sumatra = Join-Path $dir 'tools\SumatraPDF\SumatraPDF.exe'
if (-not (Test-Path $sumatra)) {
    New-Item -ItemType Directory -Force (Split-Path $sumatra) | Out-Null
    $zip = Join-Path $env:TEMP 'sumatra.zip'
    Invoke-WebRequest 'https://www.sumatrapdfreader.org/dl/rel/3.5.2/SumatraPDF-3.5.2-64.zip' -OutFile $zip -UseBasicParsing
    Expand-Archive $zip -DestinationPath (Split-Path $sumatra) -Force
    Rename-Item (Join-Path (Split-Path $sumatra) 'SumatraPDF-3.5.2-64.exe') 'SumatraPDF.exe' -Force
    Remove-Item $zip
}
Write-Host 'Done.' -ForegroundColor Green

# 4. Rent Manager login (this manager's OWN API user) ------------------------------------------
Say 'Rent Manager login'
$cfg = Join-Path $dir 'config.json'
if (-not (Test-Path $cfg)) {
    Write-Host "Enter this computer's Rent Manager API user (Codi creates one per manager, with 'API Access' ticked)."
    $u = Read-Host 'Rent Manager username'
    $sec = Read-Host 'Rent Manager password' -AsSecureString
    $p = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    $o = Read-Json (Join-Path $dir 'config.example.json')
    $o.username = $u; $o.password = $p
    Write-Json $o $cfg
    # Only this Windows user may read the file with the password in it.
    icacls $cfg /inheritance:r /grant:r "${User}:(R,W)" | Out-Null
}
New-Item -ItemType Directory -Force (Join-Path $dir 'data') | Out-Null
$ErrorActionPreference = 'Continue'      # a login error below must be reported, not crash the script
$check = & $venvPy -c "import sys; sys.path.insert(0, r'$dir'); import rmconn; c = rmconn.Connection('practice'); print('OK ' + ', '.join(sorted(p['ShortName'] for p in c.get('Properties', {'fields': 'PropertyID,ShortName'}))))" 2>&1
if ("$check" -like 'OK *') {
    Write-Host "Connected. Parks: $($check.Substring(3))" -ForegroundColor Green
} else {
    Write-Host "Could not sign in to Rent Manager: $check" -ForegroundColor Yellow
    Write-Host 'Delete config.json and run install.ps1 again to re-enter the login.'
}
$ErrorActionPreference = 'Stop'

# 5. This computer's violations settings ----------------------------------------------------
Say 'Violations settings'
$vcfg = Join-Path $dir 'violations_config.json'
if (-not (Test-Path $vcfg)) {
    $o = Read-Json (Join-Path $dir 'violations_config.example.json')
    $name = Read-Host "Manager's full name (printed on notices as 'By ...')"
    $user = Read-Host 'Username for signing in on the phone (e.g. first name)'
    if ($name) { $o.users[0].name = $name }
    if ($user) { $o.users[0].username = $user.ToLower() }
    Write-Json $o $vcfg
}
Write-Host 'Done. The phone password, park and printer are set in Violations Settings.' -ForegroundColor Green

# 6. Keep the phone server running: from Windows startup (nobody needs to sign in), at sign-in,
#    and checked every 5 minutes in case it stopped. It runs outside anyone's sign-in ("S4U",
#    no password stored), where printing doesn't work - so notices are printed by the
#    "Violations Print" task in the signed-in session (print_queue.py): at once if someone is
#    signed in, otherwise at the next sign-in. The update task below runs the same way. ---------
Say 'Phone server'
$action = New-ScheduledTaskAction -Execute $venvPyw -Argument 'mgrserver.py' -WorkingDirectory $dir
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn -User $User),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName $task -Description "Violations phone app server ($dir\mgrserver.py), port 8790." `
    -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null

$pAction = New-ScheduledTaskAction -Execute $venvPyw -Argument "`"$dir\print_queue.py`"" -WorkingDirectory $dir
$pTriggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $User),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
)
$pSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$pPrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $printTask -Description "Prints notices queued by the Violations phone server ($dir\print_queue.py)." `
    -Action $pAction -Trigger $pTriggers -Settings $pSettings -Principal $pPrincipal -Force | Out-Null
Restart-Server

# 7. Automatic updates from GitHub ------------------------------------------------------------
Say 'Automatic updates'
if (Test-Path (Join-Path $dir '.git')) {
    Write-Host "This is Codi's development copy - it isn't updated automatically."
} else {
    $uAction = New-ScheduledTaskAction -Execute $venvPyw -Argument 'updater.py' -WorkingDirectory $dir
    $uTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(7) -RepetitionInterval (New-TimeSpan -Hours 1)
    $uSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
        -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName "$task - Updates" -Description "Checks GitHub every hour for a new version of the violations server ($dir\updater.py). Never changes this computer's settings." `
        -Action $uAction -Trigger $uTrigger -Settings $uSettings -Principal $principal -Force | Out-Null
    Write-Host 'Updates are on - checked every hour.' -ForegroundColor Green
}

# 8. Desktop shortcut to Violations Settings ------------------------------------------------------
Say 'Desktop shortcut'
# On the shared desktop: this window may be running as a different (administrator) user.
$lnk = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Violations Settings.lnk'
$old = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Violations Settings.lnk'   # where older versions put it
if ((Test-Path $old) -and $old -ne $lnk) { Remove-Item $old -ErrorAction SilentlyContinue }
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $venvPyw
$s.Arguments = "`"$dir\settings_app.py`""
$s.WorkingDirectory = $dir
$s.IconLocation = "$dir\ui\phone\icon.ico,0"
$s.Description = 'Violations Server settings'
$s.Save()
Write-Host "Made 'Violations Settings' on the desktop." -ForegroundColor Green

# 9. Tailscale: the phone address (https://<name>.<tailnet>.ts.net) that works from anywhere -------
Say 'Tailscale'
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
if (-not (Test-Path $ts)) {
    Write-Host 'Installing Tailscale...'
    $msi = Get-File 'https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi' 'tailscale-setup.msi'
    Start-Process msiexec.exe -Wait -ArgumentList '/i', "`"$msi`"", '/quiet', '/norestart'
    Remove-Item $msi -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 20 -and -not (Test-Path $ts); $i++) { Start-Sleep 1 }
}
if (-not (Test-Path $ts)) {
    Write-Host "Tailscale didn't install - install it from https://tailscale.com/download/windows and run install.ps1 again." -ForegroundColor Yellow
} else {
    $ErrorActionPreference = 'Continue'
    Start-Sleep 3                                 # give the Tailscale service a moment to start
    $state = (& $ts status --json 2>$null | Out-String | ConvertFrom-Json -ErrorAction SilentlyContinue).BackendState
    if ($state -ne 'Running') {
        Write-Host ''
        Write-Host "Sign this computer in to Tailscale with the COMPANY account (the one Codi uses)." -ForegroundColor White
        $hn = Read-Host "Short name for this computer's phone address, e.g. morristown (Enter = keep '$env:COMPUTERNAME')"
        $hn = ($hn.ToLower() -replace '[^a-z0-9-]', '-').Trim('-')
        Write-Host 'A sign-in page opens in the browser (if not, hold Ctrl and click the address below).'
        Write-Host 'Waiting for the sign-in to finish...'
        # --unattended: keep Tailscale connected when nobody is signed in to Windows, like the phone server.
        $upArgs = @('up', '--unattended')
        if ($hn) { $upArgs += "--hostname=$hn" }
        & $ts @upArgs
    } else {
        & $ts set --unattended 2>&1 | Out-Null
    }
    $state = (& $ts status --json 2>$null | Out-String | ConvertFrom-Json -ErrorAction SilentlyContinue).BackendState
    if ($state -eq 'Running') {
        Write-Host 'Tailscale is signed in.' -ForegroundColor Green
        $funnel = & $ts funnel status 2>&1 | Out-String
        if ($funnel -notmatch 'Funnel on') {
            Write-Host 'Turning on Funnel (the phone address)...'
            & $ts funnel --bg 8790
        }
        $funnel = & $ts funnel status 2>&1 | Out-String
        if ($funnel -match 'Funnel on') {
            Write-Host 'Funnel is on - the phone can reach this computer from anywhere.' -ForegroundColor Green
        } else {
            Write-Host "Funnel isn't on. If a web address was shown above, Codi opens it to allow Funnel, then run install.ps1 again." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Tailscale isn't signed in yet - run install.ps1 again to finish." -ForegroundColor Yellow
    }
    $ErrorActionPreference = 'Stop'
}

Say 'All done'
Write-Host 'Opening Violations Settings - everything there should be green.'
Start-Process $venvPyw -ArgumentList "`"$dir\settings_app.py`"" -WorkingDirectory $dir
Read-Host 'Press Enter to close this window'
