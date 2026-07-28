Set-Location $PSScriptRoot
$commands = @(
    @{Exe='py'; Args=@('-3')},
    @{Exe='python'; Args=@()},
    @{Exe='python3'; Args=@()}
)
foreach ($cmd in $commands) {
    try {
        & $cmd.Exe @($cmd.Args) -c "import sys,tkinter; assert sys.version_info >= (3,10)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $cmd.Exe @($cmd.Args) boot.py
            exit $LASTEXITCODE
        }
    } catch {}
}
Write-Host 'Python 3.10+ with Tkinter not found.'
Read-Host 'Press Enter'
