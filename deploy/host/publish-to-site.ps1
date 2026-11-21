# Publishes every drafted and exported folder of the review platform to the public dataset, then uploads what changed.
# Run by the EdisonSitePublish task; it only reads the review platform's database, images and exports.
#   powershell -ExecutionPolicy Bypass -File deploy\host\publish-to-site.ps1
$ErrorActionPreference = "Stop"

$Pipeline = Join-Path $HOME "edison-pipeline"
# A worktree of edison-pipeline at the publisher's commit, so the review server's own checkout never changes.
$Publisher = Join-Path $HOME "edison-publisher"
$Archive = Join-Path $HOME "edison-archive"
$Work = Join-Path $HOME "edison-site"
$Dataset = Join-Path $Work "dataset"
$Folders = Join-Path $Work "folders.txt"
$SiteUrl = "https://thomasedisonpapers.github.io"
New-Item -ItemType Directory -Force (Join-Path $Work "logs") | Out-Null
$Log = Join-Path $Work ("logs\publish-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

function Write-Log([string]$Text) { "$(Get-Date -Format s) $Text" | Add-Content -Path $Log -Encoding UTF8 }

# Runs one program with its output in the log; Start-Process keeps PowerShell 5.1 from turning stderr lines into errors.
function Invoke-Step([string]$Name, [string]$Exe, [string[]]$Arguments) {
    Write-Log $Name
    $out = Join-Path $Work "step.out"
    $err = Join-Path $Work "step.err"
    $run = Start-Process -FilePath $Exe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    Get-Content $out, $err -ErrorAction SilentlyContinue | Add-Content -Path $Log -Encoding UTF8
    if ($run.ExitCode -ne 0) { throw "$Name failed with exit code $($run.ExitCode)" }
}

# One run at a time: the first publish of every folder takes far longer than the schedule's interval.
$lock = New-Object System.Threading.Mutex($false, "Global\EdisonSitePublish")
if (-not $lock.WaitOne(0)) { Write-Log "the previous run is still going; skipped"; exit 0 }
try {
    Set-Location $Pipeline
    $python = Join-Path $Pipeline ".venv\Scripts\python.exe"
    $env:PYTHONPATH = Join-Path $Publisher "src"
    Invoke-Step "listing every folder with a draft" $python @("-m", "edison_pipeline.cli", "public-folders",
        "--db", "data/extraction.db", "--out", $Folders)
    Invoke-Step "publishing" $python @("-m", "edison_pipeline.cli", "publish", "--db", "data/extraction.db",
        "--bucket-uri", "file://./data/bucket", "--out", $Dataset, "--folders", $Folders,
        "--exports", "data/exports/new", "--site-url", $SiteUrl)
    $env:PYTHONPATH = $null
    Invoke-Step "uploading" (Join-Path $Archive ".venv\Scripts\python.exe") @((Join-Path $Archive "builder\upload.py"),
        "--dataset", $Dataset)
    Write-Log "done"
} catch {
    Write-Log "FAILED: $_"
    exit 1
} finally {
    $lock.ReleaseMutex()
}
