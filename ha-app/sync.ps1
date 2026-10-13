# Copy the HA app (this folder plus the source) to Home Assistant's local apps folder over the Samba share.
# Then in HA: Settings > Apps > App store > menu > Check for updates, and install (or Rebuild) "psacc (fork)".
param([string]$Target = '\\192.168.8.242\addons\psacc_fork')

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$stage = Join-Path ([IO.Path]::GetTempPath()) 'psacc_fork_stage'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null

Copy-Item (Join-Path $PSScriptRoot '*') $stage -Exclude 'sync.ps1'

# Only files git tracks, so nothing local (config, logs, tokens) can end up in the image.
$files = @(git -C $repo ls-files -- psa_car_controller pyproject.toml LICENSE)
if ($files.Count -eq 0) { throw 'git ls-files found no source files' }
foreach ($file in $files) {
    $dest = Join-Path $stage "src\$file"
    New-Item -ItemType Directory (Split-Path $dest) -Force | Out-Null
    Copy-Item (Join-Path $repo $file) $dest
}

# Pin the image to the package versions in the dev venv.
& (Join-Path $repo '.venv\Scripts\python.exe') -m pip freeze --exclude-editable |
    Set-Content (Join-Path $stage 'constraints.txt') -Encoding ascii

robocopy $stage $Target /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with code $LASTEXITCODE" }
"Copied the app and $($files.Count) source files to $Target"
exit 0  # robocopy's 1-7 mean success
