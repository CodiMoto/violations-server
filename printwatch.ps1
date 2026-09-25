# Watches one job in the Windows print queue and reports what became of it, as one
# line of JSON. violations.py runs this after every print so the phone can say
# "didn't print" (and why) instead of "printing" when the printer is off, out of
# paper, jammed or no longer reachable.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File printwatch.ps1 -Printer "<name>" -Document "<pdf path>" -Seconds 120
#
# verdict   printed   the job finished (its pages came out)
#           error     the job is in the queue with a problem (Error, Offline, PaperOut, ...)
#           gone      the job left the queue (or never reached it) with nothing printed;
#                     "failed" is true if Windows logged that it gave up on this printer meanwhile
#           waiting   still in the queue with nothing printed after -Seconds
param([string]$Printer, [string]$Document, [int]$Seconds = 120)
$ErrorActionPreference = 'SilentlyContinue'
$start = Get-Date
$seen = $false; $pages = 0; $total = 0; $status = ''; $verdict = $null
$bad = 'Error|Offline|PaperOut|PaperJam|UserIntervention|Blocked|DoorOpen|NoToner|OutOfMemory'
while (((Get-Date) - $start).TotalSeconds -lt $Seconds) {
    $j = Get-PrintJob -PrinterName $Printer | Where-Object { $_.DocumentName -eq $Document } | Select-Object -First 1
    if ($j) {
        $seen = $true; $status = "$($j.JobStatus)"; $pages = [int]$j.PagesPrinted; $total = [int]$j.TotalPages
        if ($status -match 'Complete|Printed' -or ($total -gt 0 -and $pages -ge $total)) { $verdict = 'printed'; break }
        if ($status -match $bad) { $verdict = 'error'; break }
    } elseif ($seen) {
        $verdict = if ($pages -gt 0) { 'printed' } else { 'gone' }; break
    } elseif (((Get-Date) - $start).TotalSeconds -ge 20) {
        $verdict = 'gone'; break                       # 20 seconds and it never showed up
    }
    Start-Sleep -Seconds 2
}
if (-not $verdict) { $verdict = 'waiting' }
$failed = $false
if ($verdict -eq 'gone') {
    # Windows logs event 372 ("failed to print on printer ...") when it gives up on a job.
    $ev = Get-WinEvent -FilterHashtable @{LogName = 'Microsoft-Windows-PrintService/Admin'; Id = 372; StartTime = $start.AddSeconds(-5)} |
        Where-Object { $_.Message -like "*$Printer*" } | Select-Object -First 1
    $failed = [bool]$ev
}
@{ verdict = $verdict; status = $status; pages = $pages; total = $total; failed = $failed;
   seconds = [int]((Get-Date) - $start).TotalSeconds } | ConvertTo-Json -Compress
