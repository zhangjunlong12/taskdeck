# Run a program elevated (UAC) and wait for it, relaying its exit code.
# This machine has ConsentPromptBehaviorAdmin=0, so elevation is silent (no prompt).
#
# Usage (from TaskDeck task command, shell = cmd):
#   powershell -NoProfile -ExecutionPolicy Bypass -File "D:\AI\task-manager\scripts\run-elevated.ps1" ^
#     -FilePath "C:\path\to\prog.exe" -Arguments "arg1 arg2" -WorkingDirectory "D:\some\dir"
#
# Notes:
# - Environment variables do NOT survive UAC elevation, so any env the child needs
#   must be set by the child itself (e.g. via cmd /c "set X=Y&& ...") or config files.
# - The elevated process console/output is separate from TaskDeck; completion is
#   judged solely by the relayed exit code.

param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string]$Arguments = '',
    [string]$WorkingDirectory = ''
)

if ($WorkingDirectory -ne '') {
    $p = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -Verb RunAs -Wait -PassThru
} else {
    $p = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Verb RunAs -Wait -PassThru
}
exit $p.ExitCode
