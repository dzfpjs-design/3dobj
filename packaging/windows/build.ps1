# Build the one-folder app, download portable Blender 4.2 LTS, compile the installer.
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

function Get-PeSubsystem([string]$Path) {
    # IMAGE_SUBSYSTEM_WINDOWS_GUI is 2, IMAGE_SUBSYSTEM_WINDOWS_CUI is 3.
    $fs = [System.IO.File]::Open($Path, "Open", "Read", "Read")
    try {
        $fs.Seek(0x3C, "Begin") | Out-Null
        $off = New-Object byte[] 4
        [void]$fs.Read($off, 0, 4)
        $pe = [BitConverter]::ToInt32($off, 0)
        $fs.Seek($pe + 92, "Begin") | Out-Null
        $sub = New-Object byte[] 2
        [void]$fs.Read($sub, 0, 2)
        return [BitConverter]::ToUInt16($sub, 0)
    } finally {
        $fs.Close()
    }
}

$version = python -c "import tankextract; print(tankextract.__version__)"
if (-not $version) { throw "could not read tankextract version" }
Write-Host "tankextract $version"

python -m PyInstaller packaging\windows\tankextract.spec --noconfirm --clean
$dist = Join-Path $root "dist\TankExtract"
$cli = Join-Path $dist "tankextract.exe"
$gui = Join-Path $dist "TankExtractGUI.exe"
if (-not (Test-Path -LiteralPath $cli)) { throw "PyInstaller did not produce tankextract.exe" }
if (-not (Test-Path -LiteralPath $gui)) { throw "PyInstaller did not produce TankExtractGUI.exe" }
# IMAGE_SUBSYSTEM_WINDOWS_GUI is 2, IMAGE_SUBSYSTEM_WINDOWS_CUI is 3.
if ((Get-PeSubsystem $cli) -ne 3) { throw "tankextract.exe is not a console executable" }
if ((Get-PeSubsystem $gui) -ne 2) { throw "TankExtractGUI.exe is not a windowed executable" }

$blenderVersion = "4.2.14"
$zipName = "blender-$blenderVersion-windows-x64.zip"
$zipPath = Join-Path $env:TEMP $zipName
$haveZip = (Test-Path $zipPath) -and ((Get-Item $zipPath).Length -gt 50MB)
if ($haveZip) {
    Write-Host "using existing Blender zip $zipPath"
} else {
    $urls = @(
        "https://download.blender.org/release/Blender4.2/$zipName",
        "https://mirrors.aliyun.com/blender/release/Blender4.2/$zipName"
    )
    $downloaded = $false
    foreach ($url in $urls) {
        Write-Host "GET $url"
        curl.exe -fL --retry 3 --retry-delay 2 --connect-timeout 30 --max-time 1800 -o $zipPath $url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $zipPath) -and ((Get-Item $zipPath).Length -gt 50MB)) {
            $downloaded = $true
            break
        }
        Write-Host "download failed from $url"
    }
    if (-not $downloaded) { throw "could not download Blender $blenderVersion" }
}

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
