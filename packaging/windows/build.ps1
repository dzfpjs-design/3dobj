# Build the one-folder app, download portable Blender 4.2 LTS, compile the installer.
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

$version = python -c "import tankextract; print(tankextract.__version__)"
if (-not $version) { throw "could not read tankextract version" }
Write-Host "tankextract $version"

python -m PyInstaller packaging\windows\tankextract.spec --noconfirm --clean
$dist = Join-Path $root "dist\TankExtract"
if (-not (Test-Path (Join-Path $dist "tankextract.exe"))) {
    throw "PyInstaller did not produce tankextract.exe"
}
if (-not (Test-Path (Join-Path $dist "TankExtract.exe"))) {
    throw "PyInstaller did not produce TankExtract.exe"
}

$blenderVersion = "4.2.14"
$zipName = "blender-$blenderVersion-windows-x64.zip"
$zipPath = Join-Path $env:TEMP $zipName
$urls = @(
    "https://mirrors.aliyun.com/blender/release/Blender4.2/$zipName",
    "https://download.blender.org/release/Blender4.2/$zipName"
)
$downloaded = $false
foreach ($url in $urls) {
    Write-Host "GET $url"
    try {
        curl.exe -fL --retry 3 --retry-delay 2 -o $zipPath $url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $zipPath) -and ((Get-Item $zipPath).Length -gt 50MB)) {
            $downloaded = $true
            break
        }
    } catch {
        Write-Host "download failed: $_"
    }
}
if (-not $downloaded) { throw "could not download Blender $blenderVersion" }

$stage = Join-Path $env:TEMP "tankextract-blender"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null
tar -xf $zipPath -C $stage
$extracted = Get-ChildItem $stage -Directory | Select-Object -First 1
if (-not $extracted) { throw "Blender zip has no top folder" }
$target = Join-Path $dist "blender"
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
Move-Item $extracted.FullName $target
$blenderExe = Join-Path $target "blender.exe"
if (-not (Test-Path $blenderExe)) { throw "blender.exe missing after extract" }
& $blenderExe --version
if ($LASTEXITCODE -ne 0) { throw "bundled blender --version failed" }

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup ISCC.exe not found" }
& $iscc "/DMyAppVersion=$version" (Join-Path $PSScriptRoot "tankextract.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

$setup = Join-Path $root "dist\installer\TankExtract-Setup-$version.exe"
if (-not (Test-Path $setup)) { throw "installer missing: $setup" }
$bytes = (Get-Item $setup).Length
Write-Host ("INSTALLER {0} ({1:N1} MB)" -f $setup, ($bytes / 1MB))
Set-Content -Path (Join-Path $root "dist\installer\blender-version.txt") -Value $blenderVersion -Encoding ascii
Set-Content -Path (Join-Path $root "dist\installer\size-bytes.txt") -Value "$bytes" -Encoding ascii
