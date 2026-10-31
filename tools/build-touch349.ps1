param([string]$EspRoot=(Join-Path $env:USERPROFILE 'esp'))
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$env:IDF_PATH=Join-Path $EspRoot 'esp-idf'
$env:IDF_TOOLS_PATH=Join-Path $EspRoot 'tools'
$env:IDF_PYTHON_ENV_PATH=Join-Path $EspRoot 'tools\python_env\idf5.3_py3.12_env'
$env:IDF_PYTHON_CHECK_CONSTRAINTS='no'
$env:IDF_SKIP_CHECK_SUBMODULES='1'
$env:PYTHONUTF8='1'
$env:Path="$env:IDF_PYTHON_ENV_PATH\Scripts;$EspRoot\tools\tools\cmake\3.30.2\bin;$EspRoot\tools\tools\ninja\1.12.1;$EspRoot\tools\tools\xtensa-esp-elf\esp-13.2.0_20240530\xtensa-esp-elf\bin;$env:Path"
$taskPython=Join-Path $env:IDF_PYTHON_ENV_PATH 'Scripts\python.exe'
$taskStage=Join-Path $env:TEMP 'desk-touch349-standalone-source'
$taskBuild=Join-Path $env:TEMP 'desk-touch349-standalone-build'
New-Item -ItemType Directory -Force -Path $taskStage | Out-Null
foreach($taskName in @('CMakeLists.txt','sdkconfig.defaults','partitions.csv','dependencies.lock','main','components')) {
    Copy-Item -LiteralPath (Join-Path $taskRoot "firmware\$taskName") -Destination $taskStage -Recurse -Force
}
Push-Location $taskStage
try {
    & $taskPython "$env:IDF_PATH\tools\idf.py" -B $taskBuild build
    if($LASTEXITCODE -ne 0) { throw 'Touch349 firmware build failed.' }
    $taskDist=Join-Path $taskRoot 'firmware\dist'
    New-Item -ItemType Directory -Force -Path $taskDist | Out-Null
    foreach($taskFile in @('desk_companion_touch349.bin','bootloader\bootloader.bin','partition_table\partition-table.bin','flasher_args.json')) {
        Copy-Item -LiteralPath (Join-Path $taskBuild $taskFile) -Destination $taskDist
    }
    Write-Output "Build directory: $taskBuild"
    Write-Output 'No device was flashed. Follow docs/development.md to select and verify your own board.'
} finally { Pop-Location }
