param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string]$Arguments = '',
    [string]$WorkingDirectory = ''
)

$log = 'C:\Users\admin\AppData\Local\Temp\elev_dbg.txt'
"invoked: FilePath=$FilePath | Args=$Arguments | WD=$WorkingDirectory" | Out-File -Encoding utf8 $log
try {
    if ($WorkingDirectory -ne '') {
        $p = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -Verb RunAs -Wait -PassThru
    } else {
        $p = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Verb RunAs -Wait -PassThru
    }
    "startproc ok, exitcode=$($p.ExitCode)" | Out-File -Encoding utf8 -Append $log
    exit $p.ExitCode
} catch {
    $_.ToString() | Out-File -Encoding utf8 -Append $log
    exit 99
}
