# Install the built setup and exercise the CLI on a Chinese path plus the repo fixtures.
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Get-PeSubsystem([string]$Path) {
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
$setup = Join-Path $root "dist\installer\TankExtract-Setup-$version.exe"
if (-not (Test-Path $setup)) { throw "installer not found: $setup" }

$install = "C:\TankExtract"
if (Test-Path $install) { Remove-Item -Recurse -Force $install }
$proc = Start-Process -FilePath $setup -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$install"
) -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "installer exit $($proc.ExitCode)" }

$exe = Join-Path $install "tankextract.exe"
$gui = Join-Path $install "TankExtractGUI.exe"
$blender = Join-Path $install "blender\blender.exe"
foreach ($path in @($exe, $gui, $blender)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "missing after install: $path" }
}
if ((Get-PeSubsystem $exe) -ne 3) { throw "installed tankextract.exe is not a console program" }
if ((Get-PeSubsystem $gui) -ne 2) { throw "installed TankExtractGUI.exe is not a windowed program" }

Write-Host "bundled blender:"
& $blender --version
if ($LASTEXITCODE -ne 0) { throw "bundled blender failed" }
if (-not (Test-Path (Join-Path $install "_internal"))) {
    Write-Host "install tree:"
    Get-ChildItem $install | ForEach-Object { Write-Host $_.Name }
    throw "missing _internal next to tankextract.exe"
}

function Invoke-Tankextract([string[]]$TankArgs, [string]$Label) {
    Write-Host ("RUN {0}: {1} {2}" -f $Label, $exe, ($TankArgs -join " "))
    # Call the console exe directly so PowerShell waits and the log streams.
    # Start-Process -Wait -RedirectStandard* deadlocks when the pipe fills.
    & $exe @TankArgs
    if ($LASTEXITCODE -ne 0) { throw "$Label failed: $LASTEXITCODE" }
}

Write-Host "RUN version"
$versionText = & $exe --version | Out-String
Write-Host $versionText
if ($LASTEXITCODE -ne 0) { throw "tankextract --version failed: $LASTEXITCODE" }
if ($versionText -notmatch "tankextract") { throw "tankextract --version produced no banner: $versionText" }

# Build the Chinese folder name in code so this ASCII script still exercises Unicode paths.
$work = "C:\" + [string]::new([char[]](0x6765, 0x5B89, 0x6A21, 0x578B))
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
New-Item -ItemType Directory -Path $work | Out-Null
$sample = Join-Path $work ([string]::new([char[]](0x6837, 0x672C)))
Copy-Item -Recurse (Join-Path $root "tests\fixtures") $sample
# Drop macOS junk if any slipped into the tree.
Get-ChildItem -Recurse $sample -Force | Where-Object { $_.Name.StartsWith("._") -or $_.Name -eq "__MACOSX" } | Remove-Item -Recurse -Force

$fixtureOut = Join-Path $work ([string]::new([char[]](0x6837, 0x672C, 0x8F93, 0x51FA)))
Invoke-Tankextract @(
    "--input", $sample, "--output", $fixtureOut, "--reader", "python",
    "--no-blender", "--no-structures", "--no-connectors", "--limit-tiles", "4"
) "fixture"
$stats = Get-Content (Join-Path $fixtureOut "read_stats.json") -Raw -Encoding UTF8
if ($stats -notmatch '"tiles"\s*:\s*[1-9]') { throw "fixture run did not read any tile: $stats" }
Write-Host "fixture tiles ok"

$zip = Join-Path $work (([string]::new([char[]](0x6837, 0x672C))) + ".zip")
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $sample "*") -DestinationPath $zip
$zipOut = Join-Path $work ("zip" + [string]::new([char[]](0x8F93, 0x51FA)))
Invoke-Tankextract @(
    "--input", $zip, "--output", $zipOut, "--reader", "python",
    "--no-blender", "--no-structures", "--no-connectors", "--limit-tiles", "4"
) "zip"
Write-Host "unicode zip ok"

$self = Join-Path $work ([string]::new([char[]](0x81EA, 0x68C0)))
Invoke-Tankextract @("--self-test", "--output", $self, "--reader", "python", "--local-offset", "--no-tree-filter") "selftest"
$fbx = Join-Path $self "tanks\Tank_001.fbx"
$report = Join-Path $self "tanks\blender_report.json"
if (-not (Test-Path $fbx)) { throw "self-test did not write $fbx" }
if (-not (Test-Path $report)) { throw "self-test did not write blender_report.json" }
$head = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes($fbx)[0..17])
if ($head -ne "Kaydara FBX Binary") { throw "FBX magic is '$head'" }
$geo = Get-Content (Join-Path $self "tanks\tanks.geojson") -Raw -Encoding UTF8
if ($geo -notmatch "Tank_003") { throw "geojson missing Tank_003" }
Write-Host "self-test FBX ok"
Write-Host "SMOKE OK"
