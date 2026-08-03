param(
    [switch]$LiveInternet,
    [switch]$LiveModel,
    [switch]$VerboseOutput,
    [string[]]$Group,
    [string]$JsonReport
)

$ErrorActionPreference = "Stop"
$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$Arguments = @(
    "$ScriptDirectory\regression_suite.py",
    "--project-root",
    $ScriptDirectory
)

foreach ($SelectedGroup in $Group) {
    $Arguments += @("--group", $SelectedGroup)
}

if ($LiveInternet) {
    $Arguments += "--live-internet"
}

if ($LiveModel) {
    $Arguments += "--live-model"
}

if ($VerboseOutput) {
    $Arguments += "--verbose"
}

if ($JsonReport) {
    $Arguments += @("--json-report", $JsonReport)
}

& python @Arguments
exit $LASTEXITCODE
