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

# Keep argument boundaries for paths containing spaces and use the program's exit code to detect failure.
function Invoke-Step([string]$Name, [string]$Exe, [string[]]$Arguments) {
    Write-Log $Name
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw "$Exe not found" }
    & {
        $ErrorActionPreference = "Continue"
        & $Exe @Arguments 2>&1
    } | ForEach-Object { $_.ToString() } | Add-Content -LiteralPath $Log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

# One run at a time: the first publish of every folder takes far longer than the schedule's interval.
$lock = New-Object System.Threading.Mutex($false, "Global\EdisonSitePublish")
$acquired = $false
try {
    try { $acquired = $lock.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { Write-Log "the previous run is still going; skipped"; exit 0 }
    Set-Location $Pipeline
    $python = Join-Path $Pipeline ".venv\Scripts\python.exe"
    $env:PYTHONPATH = Join-Path $Publisher "src"
    Invoke-Step "listing every folder with a draft" $python @("-m", "edison_pipeline.cli", "public-folders",
        "--db", "data/extraction.db", "--out", $Folders)
    Invoke-Step "publishing" $python @("-m", "edison_pipeline.cli", "publish", "--db", "data/extraction.db",
        "--bucket-uri", "file://./data/bucket", "--out", $Dataset, "--folders", $Folders,
        "--exports", "data/exports/new", "--site-url", $SiteUrl)
    Invoke-Step "publishing account books and notebooks" $python @("-m", "edison_pipeline.cli", "publish-volumes", "--db", "data/extraction.db",
        "--bucket-uri", "file://./data/bucket", "--out", $Dataset)
    $env:PYTHONPATH = $null
    $archivePython = Join-Path $Archive ".venv\Scripts\python.exe"
    $embed = Join-Path $Archive "builder\embed.py"
    $store = Join-Path $Dataset "embeddings"
    $upload = Join-Path $Archive "builder\upload.py"
    $mirror = Join-Path $Archive "builder\mirror_scans.py"
    Invoke-Step "reconciling cached scans before upload" $archivePython @($mirror, "--dataset", $Dataset, "--prune-only")
    # Publish records independently if embedding fails so withdrawals never wait for the model.
    $uploadModel = "arctic-embed-s"
    $recordsUploaded = $false
    try {
        Invoke-Step "planning embeddings" $archivePython @($embed, "plan", "--dataset", $Dataset, "--store", $store,
            "--model", "arctic-embed-s", "--scope", "transcribed", "--collections", "accounts,notebooks")
        $todo = Join-Path $store "arctic-embed-s\todo"
        if (Test-Path -LiteralPath (Join-Path $todo "chunk-00001.jsonl") -PathType Leaf) {
            # A large backfill can outlast the publish interval, so make the records visible before encoding.
            Invoke-Step "uploading records before embedding backfill" $archivePython @($upload, "--dataset", $Dataset,
                "--embedding-model", "none")
            $recordsUploaded = $true
        }
        Invoke-Step "encoding new passages" $archivePython @($embed, "encode", "--store", $store, "--model", "arctic-embed-s",
            "--backend", "onnx", "--batch", "16")
        Invoke-Step "collecting embeddings" $archivePython @($embed, "collect", "--store", $store, "--model", "arctic-embed-s")
        Invoke-Step "finding related documents" $archivePython @($embed, "related", "--store", $store, "--model", "arctic-embed-s")
    } catch {
        Write-Log "embedding skipped this run: $_"
        $uploadModel = "none"
    }
    if (-not $recordsUploaded -or $uploadModel -ne "none") {
        Invoke-Step "uploading" $archivePython @($upload, "--dataset", $Dataset, "--embedding-model", $uploadModel)
    }
    # After the upload, so a slow or failing edisondigital never holds up approvals or withdrawals; new scans go up next run.
    try {
        Invoke-Step "mirroring account-book scans" $archivePython @($mirror, "--dataset", $Dataset,
            "--contact", "$SiteUrl/about.html", "--minutes", "8")
    } catch {
        Write-Log "mirroring skipped this run: $_"
    }
    Write-Log "done"
} catch {
    Write-Log "FAILED: $_"
    exit 1
} finally {
    if ($acquired) { $lock.ReleaseMutex() }
    $lock.Dispose()
}
