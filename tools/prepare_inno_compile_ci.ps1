# Provision only the pinned vendor compiler in a disposable hosted runner.
# Never launches Xingtu Setup, changes its registration, or uses PATH shims.
param([Parameter(Mandatory=$true)][string]$OutputRoot)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:RUNNER_OS -ne 'Windows') { throw 'Hosted Windows CI only' }
$base = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\')
$root = [IO.Path]::GetFullPath($OutputRoot)
if (-not $root.StartsWith($base + '\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $root)) { throw 'A new child of RUNNER_TEMP is required' }
New-Item -ItemType Directory -Path $root | Out-Null
$installer = Join-Path $root 'innosetup-6.7.3.exe'
$compilerRoot = Join-Path $root 'compiler'
$url = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
$installerHash = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
$compilerHash = '0a8757031b33777e4c9cbffee40f11a5062b36d25cbe144c1db73b6102b80ad7'
function Assert-PyrsysSignature([string]$Path) {
  $s = Get-AuthenticodeSignature -LiteralPath $Path
  if ($s.Status -ne 'Valid' -or -not $s.SignerCertificate -or $s.SignerCertificate.Subject -notmatch '(^|,\s*)CN=Pyrsys B\.V\.(,|$)') { throw 'Pinned vendor signature is not valid Pyrsys B.V.' }
  return @{ status=[string]$s.Status; subject=$s.SignerCertificate.Subject; thumbprint=$s.SignerCertificate.Thumbprint }
}
Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
if ((Get-Item -LiteralPath $installer).Length -ne 10592232 -or (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $installerHash) { throw 'Pinned Inno installer bytes differ' }
$outerSignature = Assert-PyrsysSignature $installer
# Native vendor portable mode: no prior installation reuse, uninstaller entry,
# file association, or IDE launch. This is NOT a Xingtu installer invocation.
$arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART', '/CURRENTUSER', '/PORTABLE=1', '/NOICONS', '/TASKS=""', ('/DIR="' + $compilerRoot + '"'), ('/LOG="' + (Join-Path $root 'vendor-prepare.log') + '"'))
$p = Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Hidden -PassThru
if (-not $p.WaitForExit(120000)) { throw 'Vendor preparation timed out; stop this disposable job' }
if ($p.ExitCode -ne 0) { throw ('Vendor preparation failed: ' + $p.ExitCode) }
$iscc = Join-Path $compilerRoot 'ISCC.exe'
if ((Get-FileHash -LiteralPath $iscc -Algorithm SHA256).Hash.ToLowerInvariant() -ne $compilerHash) { throw 'Actual ISCC differs from the previously verified 6.7.3 binary' }
$innerSignature = Assert-PyrsysSignature $iscc
# The vendor distribution omits Languages/Unofficial. Preserve the original
# production Inno language reference using the matching source-tag translation.
# This is a community translation, not an officially supported Inno language.
$languageCommit = '4adf37ed7f3fd2bd11c6836ba056e3de170fbabf'
$languageSourcePath = 'Files/Languages/Unofficial/ChineseSimplified.isl'
$languageBlob = 'd6a11c4490de07dad443ade668289fc954dfa1ed'
$languageUrl = 'https://raw.githubusercontent.com/jrsoftware/issrc/' + $languageCommit + '/' + $languageSourcePath
$languageHash = '7d544b9bb1d142cfa11f2e5d3cc8abe2e55f8e066c5124e3772675aa236e1278'
$languageDownload = Join-Path $root 'ChineseSimplified.isl.download'
$languageDirectory = Join-Path $compilerRoot 'Languages'
$languageTarget = Join-Path $languageDirectory 'ChineseSimplified.isl'
$languageDirInfo = Get-Item -LiteralPath $languageDirectory
if (-not $languageDirInfo.PSIsContainer -or ($languageDirInfo.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or (Test-Path -LiteralPath $languageTarget)) { throw 'Expected a regular language directory and an absent Chinese catalog' }
# OutFile preserves the UTF-8 (no BOM) source bytes. Never transcode based on
# LanguageCodePage, change line endings, use a floating URL or fall back to English.
Invoke-WebRequest -Uri $languageUrl -OutFile $languageDownload -UseBasicParsing
if ((Get-Item -LiteralPath $languageDownload).Length -ne 20905 -or (Get-FileHash -LiteralPath $languageDownload -Algorithm SHA256).Hash.ToLowerInvariant() -ne $languageHash) { throw 'Pinned Chinese language catalog bytes differ' }
Move-Item -LiteralPath $languageDownload -Destination $languageTarget
if ((Get-Item -LiteralPath $languageTarget).Length -ne 20905 -or (Get-FileHash -LiteralPath $languageTarget -Algorithm SHA256).Hash.ToLowerInvariant() -ne $languageHash) { throw 'Prepared Chinese language catalog bytes differ' }
$languageProvenance = @{ repository='jrsoftware/issrc'; sourceTag='is-6_7_3'; sourceCommit=$languageCommit; sourcePath=$languageSourcePath; gitBlob=$languageBlob; sourceUrl=$languageUrl; classification='unofficial-community-translation'; bytes=20905; sha256=$languageHash; target='Languages\ChineseSimplified.isl'; originalBytesPreserved=$true }
$rows = @(Get-ChildItem -LiteralPath $compilerRoot -Recurse -File | ForEach-Object {
  if (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Compiler payload contains reparse point' }
  @{ path=$_.FullName.Substring($compilerRoot.Length + 1); bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
@{ schema='xingtu-compiler-ci-provenance-v1'; version='6.7.3'; installerUrl=$url; installerSha256=$installerHash; installerBytes=10592232; installerSignature=$outerSignature; compilerSha256=$compilerHash; compilerSignature=$innerSignature; preparationMode='vendor-portable'; supplementaryLanguage=$languageProvenance; files=$rows; xingtuSetupStarted=$false } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $root 'compiler-provenance.json') -Encoding UTF8
Write-Output 'Pinned Inno 6.7.3 prepared and verified; no Xingtu Setup started.'
