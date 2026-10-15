param([int]$Port = 8123)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $projectPath '.local-ha/config'
$componentPath = Join-Path $projectPath 'custom_components'
$imageName = 'ghcr.io/home-assistant/home-assistant:2025.12.4'
$containerName = 'medicover-ha-local'
$authChanged = $false

New-Item -ItemType Directory -Force -Path $configPath | Out-Null
$configurationPath = Join-Path $configPath 'configuration.yaml'
if (-not (Test-Path -LiteralPath $configurationPath)) {
    @'
homeassistant:
  name: Medi Assistant — test lokalny
  time_zone: Europe/Warsaw
  country: PL
  unit_system: metric
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 127.0.0.1
        - ::1
        - 172.17.0.1/32
      allow_bypass_login: true
    - type: homeassistant
frontend:
config:
history:
logbook:
my:
mobile_app:
person:
system_health:
automation: !include automations.yaml
logger:
  default: warning
'@ | Set-Content -LiteralPath $configurationPath -Encoding utf8
    '[]' | Set-Content -LiteralPath (Join-Path $configPath 'automations.yaml') -Encoding utf8
}
# Upgrade only the configuration created by this local launcher.
$localConfiguration = Get-Content -LiteralPath $configurationPath -Raw
if ($localConfiguration -notmatch 'auth_providers:') {
    $trustedAuth = @'
homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 127.0.0.1
        - ::1
        - 172.17.0.1/32
      allow_bypass_login: true
    - type: homeassistant
'@
    $localConfiguration = $localConfiguration -replace '(?m)^homeassistant:\r?\n', ($trustedAuth + "`n")
    Set-Content -LiteralPath $configurationPath -Value $localConfiguration -Encoding utf8
    $authChanged = $true
}
$exists = docker ps -a --filter "name=^/$containerName`$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker nie odpowiada.' }
if ($exists -eq $containerName) {
    $mounts = docker inspect $containerName --format '{{json .Mounts}}' | ConvertFrom-Json
    $configMount = $mounts | Where-Object { $_.Destination -eq '/config' }
    $componentMount = $mounts | Where-Object { $_.Destination -eq '/config/custom_components' }
    if ($configMount.Source -ne $configPath -or $componentMount.Source -ne $componentPath) {
        throw 'Kontener korzysta z innej kopii projektu. Przerywam, aby nie uruchomić niewłaściwej wersji.'
    }
    if ($authChanged) { docker restart $containerName } else { docker start $containerName }
} else {
    docker run -d --name $containerName `
        -p "127.0.0.1:${Port}:8123" `
        -e 'TZ=Europe/Warsaw' `
        --mount "type=bind,source=$configPath,target=/config" `
        --mount "type=bind,source=$componentPath,target=/config/custom_components,readonly" `
        $imageName
}
if ($LASTEXITCODE -ne 0) { throw 'Nie udało się uruchomić Home Assistant.' }
docker cp (Join-Path $PSScriptRoot 'prepare_local_ha.py') "${containerName}:/tmp/prepare_local_ha.py"
if ($LASTEXITCODE -ne 0) { throw 'Nie udało się przygotować lokalnego użytkownika.' }
docker exec $containerName python /tmp/prepare_local_ha.py
if ($LASTEXITCODE -ne 0) { throw 'Nie udało się zakończyć lokalnego onboardingu.' }
Write-Output "Home Assistant: http://localhost:$Port"
Write-Output "Konfiguracja i dane logowania są tylko w ignorowanym katalogu .local-ha/config."
