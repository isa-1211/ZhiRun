param(
    [string]$SdkRoot = (Join-Path $PSScriptRoot "..\downloads\android-sdk"),
    [string]$JdkRoot = "C:\Program Files\Eclipse Adoptium\jdk-17.0.17.10-hotspot",
    [switch]$MappedPath
)

$ErrorActionPreference = "Stop"

# Android's Windows resource tools still fail on some non-ASCII paths. Build through
# a temporary drive mapping while keeping every source and artifact in the repository.
if (-not $MappedPath -and $PSScriptRoot -match "[^\x00-\x7F]") {
    $workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $driveLetter = @("Z", "Y", "X", "W") | Where-Object { -not (Test-Path ("{0}:\" -f $_)) } | Select-Object -First 1
    if (-not $driveLetter) { throw "No free drive letter is available for the Android build" }
    $drive = "${driveLetter}:"
    & subst.exe $drive $workspaceRoot
    if ($LASTEXITCODE -ne 0) { throw "Unable to map the project to an ASCII-only path" }
    try {
        $mappedScript = "$drive\android-app\build-apk.ps1"
        $mappedSdk = "$drive\downloads\android-sdk"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $mappedScript `
            -SdkRoot $mappedSdk -JdkRoot $JdkRoot -MappedPath
        if ($LASTEXITCODE -ne 0) { throw "Mapped Android build failed" }
    } finally {
        & subst.exe $drive /D | Out-Null
    }
    exit 0
}

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$sdkRootResolved = (Resolve-Path $SdkRoot).Path
$jdkRootResolved = (Resolve-Path $JdkRoot).Path
$buildTools = Join-Path $sdkRootResolved "build-tools\35.0.0"
$androidJar = Join-Path $sdkRootResolved "platforms\android-35\android.jar"
$manifest = Join-Path $projectRoot "app\src\main\AndroidManifest.xml"
$resources = Join-Path $projectRoot "app\src\main\res"
$sources = Join-Path $projectRoot "app\src\main\java"
$buildDir = Join-Path $projectRoot "build\direct"
$generatedDir = Join-Path $buildDir "generated"
$classesDir = Join-Path $buildDir "classes"
$dexDir = Join-Path $buildDir "dex"
$distDir = Join-Path $projectRoot "dist"

foreach ($required in @(
    $androidJar,
    (Join-Path $buildTools "aapt2.exe"),
    (Join-Path $buildTools "d8.bat"),
    (Join-Path $buildTools "zipalign.exe"),
    (Join-Path $buildTools "apksigner.bat"),
    (Join-Path $jdkRootResolved "bin\javac.exe")
)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing Android build dependency: $required"
    }
}

if (Test-Path -LiteralPath $buildDir) {
    $resolvedBuildDir = (Resolve-Path $buildDir).Path
    if (-not $resolvedBuildDir.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to clean unexpected build directory: $resolvedBuildDir"
    }
    Remove-Item -LiteralPath $resolvedBuildDir -Recurse -Force
}
New-Item -ItemType Directory -Force $generatedDir, $classesDir, $dexDir, $distDir | Out-Null

$aapt2 = Join-Path $buildTools "aapt2.exe"
$compiledResources = Join-Path $buildDir "resources.zip"
$unsignedApk = Join-Path $buildDir "unsigned.apk"
$alignedApk = Join-Path $buildDir "aligned.apk"
$releaseApk = Join-Path $distDir "ZhiRun-1.0.0.apk"

& $aapt2 compile --dir $resources -o $compiledResources
if ($LASTEXITCODE -ne 0) { throw "aapt2 resource compilation failed" }

& $aapt2 link -o $unsignedApk -I $androidJar --manifest $manifest --java $generatedDir `
    --min-sdk-version 24 --target-sdk-version 35 --version-code 1 --version-name "1.0.0" `
    --auto-add-overlay $compiledResources
if ($LASTEXITCODE -ne 0) { throw "aapt2 APK linking failed" }

$javaSources = @(
    Get-ChildItem -LiteralPath $sources -Recurse -Filter "*.java" | ForEach-Object FullName
    Get-ChildItem -LiteralPath $generatedDir -Recurse -Filter "*.java" | ForEach-Object FullName
)
$javac = Join-Path $jdkRootResolved "bin\javac.exe"
& $javac -encoding UTF-8 --release 8 -classpath $androidJar -d $classesDir @javaSources
if ($LASTEXITCODE -ne 0) { throw "Java compilation failed" }

$classFiles = @(Get-ChildItem -LiteralPath $classesDir -Recurse -Filter "*.class" | ForEach-Object FullName)
$env:JAVA_HOME = $jdkRootResolved
& (Join-Path $buildTools "d8.bat") --lib $androidJar --min-api 24 --output $dexDir @classFiles
if ($LASTEXITCODE -ne 0) { throw "DEX compilation failed" }

Push-Location $dexDir
try {
    & (Join-Path $buildTools "aapt.exe") add $unsignedApk "classes.dex"
    if ($LASTEXITCODE -ne 0) { throw "Adding classes.dex failed" }
} finally {
    Pop-Location
}

& (Join-Path $buildTools "zipalign.exe") -f -p 4 $unsignedApk $alignedApk
if ($LASTEXITCODE -ne 0) { throw "APK alignment failed" }

$signingFile = Join-Path $projectRoot "signing.properties"
if (-not (Test-Path -LiteralPath $signingFile)) {
    throw "Missing signing.properties; a release APK must use the project signing key"
}
$signing = @{}
Get-Content -LiteralPath $signingFile -Encoding UTF8 | ForEach-Object {
    if ($_ -match "^([^#=]+)=(.*)$") { $signing[$matches[1].Trim()] = $matches[2].Trim() }
}
$keystore = Join-Path $projectRoot $signing.storeFile
& (Join-Path $buildTools "apksigner.bat") sign `
    --ks $keystore --ks-key-alias $signing.keyAlias `
    --ks-pass ("pass:" + $signing.storePassword) --key-pass ("pass:" + $signing.keyPassword) `
    --out $releaseApk $alignedApk
if ($LASTEXITCODE -ne 0) { throw "APK signing failed" }

& (Join-Path $buildTools "apksigner.bat") verify --verbose --print-certs $releaseApk
if ($LASTEXITCODE -ne 0) { throw "APK signature verification failed" }

$artifact = Get-Item -LiteralPath $releaseApk
Write-Host ("APK ready: {0} ({1:N2} MB)" -f $artifact.FullName, ($artifact.Length / 1MB))
