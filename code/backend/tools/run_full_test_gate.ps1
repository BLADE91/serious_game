param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
if ([System.IO.Path]::IsPathRooted($EvidenceRoot)) {
    $ResolvedEvidenceRoot = [System.IO.Path]::GetFullPath($EvidenceRoot)
} else {
    $ResolvedEvidenceRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $RepoRoot $EvidenceRoot)
    )
}
$GateRoot = Join-Path $ResolvedEvidenceRoot "full-test-gate"
New-Item -ItemType Directory -Force -Path $GateRoot | Out-Null
$RunStamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")

function Invoke-FullGateStage {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    $LogPath = Join-Path $GateRoot "$RunStamp-$Name.log"
    Write-Host "[$Name] starting"
    & $Command 2>&1 | Tee-Object -FilePath $LogPath
    $ExitCode = $LASTEXITCODE
    if ($null -eq $ExitCode) { $ExitCode = 0 }
    if ($ExitCode -ne 0) {
        throw "full test gate stage '$Name' failed with exit code $ExitCode"
    }
    Write-Host "[$Name] passed"
}

try {
    Push-Location $RepoRoot
    try {
        Invoke-FullGateStage "launcher" { cmd /c "BEGIN.BAT --check" }
    } finally {
        Pop-Location
    }

    Push-Location (Join-Path $RepoRoot "code\backend")
    try {
        $env:PYTHONPATH = "src"
        $StoryRoutesSummary = Join-Path $GateRoot "$RunStamp-backend-story-routes-v3.json"
        $env:STORY_ROUTES_V3_SUMMARY = $StoryRoutesSummary
        try {
            Invoke-FullGateStage "backend" { python -m pytest -q }
        } finally {
            Remove-Item Env:STORY_ROUTES_V3_SUMMARY -ErrorAction SilentlyContinue
        }
        Invoke-FullGateStage "v2-bytes" {
            python tools/hash_content_tree.py content/packages/pkg_gameplay_v2 --compare ../../docs/testing/baselines/pkg_gameplay_v2.sha256.json
        }
    } finally {
        Pop-Location
    }

    Push-Location (Join-Path $RepoRoot "code\frontend\web")
    try {
        Invoke-FullGateStage "web-tests" { npm test }
        Invoke-FullGateStage "web-build" { npm run build }
        Invoke-FullGateStage "web-lint" { npm run lint }
    } finally {
        Pop-Location
    }

    Push-Location (Join-Path $RepoRoot "code\frontend\terminal")
    try {
        $env:PYTHONPATH = "."
        Invoke-FullGateStage "terminal" { python -m pytest -q }
    } finally {
        Pop-Location
    }

    Push-Location (Join-Path $RepoRoot "code\backend")
    try {
        Invoke-FullGateStage "secret-scan" {
            python tools/check_secret_leaks.py ../.. --evidence-root $ResolvedEvidenceRoot
        }
    } finally {
        Pop-Location
    }
} catch {
    Write-Error $_
    exit 1
}

Write-Host "Full test gate passed. Evidence logs: $GateRoot"
exit 0
