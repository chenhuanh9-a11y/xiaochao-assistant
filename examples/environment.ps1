# Example only. Replace this path with your own authorized, version-matched resource root.
$resourceRoot = Join-Path $PSScriptRoot '..\resources-private'
$env:IDENTITYV_NIGHTMARE_RESOURCE_ROOT = [IO.Path]::GetFullPath($resourceRoot)
$env:IDENTITYV_SHARED_STRUCTURE_ROOT = $env:IDENTITYV_NIGHTMARE_RESOURCE_ROOT
$env:IDENTITYV_SHARED_STRUCTURE_INDEX = Join-Path $resourceRoot 'references\nightmare-shared-structure-index.json'
# Optional override for historical prototype modules; no legacy assets are provided.
$env:IDENTITYV_LEGACY_ASSETS_ROOT = Join-Path $resourceRoot 'assets\legacy'
