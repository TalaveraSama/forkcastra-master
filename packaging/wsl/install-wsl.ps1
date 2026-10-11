<#
.SYNOPSIS
    Instala y arranca Astra WebUI dentro de WSL desde Windows.

.DESCRIPTION
    Orquesta todo el proceso desde el lado de Windows:

      1. comprueba que WSL puede funcionar (característica WSL y plataforma de VM),
      2. comprueba/instala la distribución de Ubuntu,
      3. copia este proyecto dentro de WSL (en /root/astra-webui),
      4. ejecuta packaging/wsl/setup-in-wsl.sh (dependencias, compilar el núcleo,
         construir el .deb, instalar, arrancar y probar de extremo a extremo),
      5. muestra la URL del panel, la contraseña inicial y, si se pide, expone los
         puertos a la LAN y crea una tarea programada para que arranque solo.

.PARAMETER Distro
    Nombre de la distribución WSL (por defecto Ubuntu-22.04).

.PARAMETER ProjectDir
    Ruta de Windows con este proyecto (por defecto, el directorio padre de packaging/).

.PARAMETER InstallDistro
    Instala la distribución si no existe (puede requerir reiniciar Windows).

.PARAMETER ExposeToLan
    Añade portproxy + regla de firewall para abrir el panel desde el navegador de
    Windows o desde otros equipos de la red.

.PARAMETER AutoStart
    Crea una tarea programada que arranca astra y el panel al iniciar Windows.

.PARAMETER SkipDeps
    No ejecutar apt-get dentro de WSL.

.PARAMETER Check
    Sólo comprobar requisitos y mostrar qué haría, sin cambiar nada.

.EXAMPLE
    .\install-wsl.ps1 -InstallDistro -ExposeToLan

.EXAMPLE
    .\install-wsl.ps1 -Distro Ubuntu-24.04 -AutoStart
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Distro = 'Ubuntu-22.04',
    [string]$ProjectDir = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [switch]$InstallDistro,
    [switch]$ExposeToLan,
    [switch]$AutoStart,
    [switch]$SkipDeps,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$script:Problems = 0

function Write-Step  { param([string]$Text) Write-Host "`n== $Text" -ForegroundColor Cyan }
function Write-Ok    { param([string]$Text) Write-Host "  OK   $Text" -ForegroundColor Green }
function Write-Info  { param([string]$Text) Write-Host "  ·    $Text" -ForegroundColor Gray }
function Write-Warn2 { param([string]$Text) Write-Host "  AVISO $Text" -ForegroundColor Yellow }
function Write-Bad   { param([string]$Text) Write-Host "  FALLO $Text" -ForegroundColor Red; $script:Problems++ }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object -TypeName Security.Principal.WindowsPrincipal -ArgumentList $identity
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Wsl {
    param([string[]]$Arguments, [switch]$Quiet, [int]$TimeoutSeconds = 0)
    $exe = (Get-Command wsl.exe -ErrorAction SilentlyContinue)
    if (-not $exe) { throw 'no se encuentra wsl.exe' }
    $stdout = [System.IO.Path]::GetTempFileName()
    $stderr = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath $exe.Source -ArgumentList $Arguments -NoNewWindow -Wait `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $out = (Get-Content -Raw -Path $stdout -ErrorAction SilentlyContinue)
        $err = (Get-Content -Raw -Path $stderr -ErrorAction SilentlyContinue)
        if (-not $Quiet -and $out) { Write-Host ($out.TrimEnd()) }
        if ($err -and $err.Trim()) { Write-Host ($err.TrimEnd()) -ForegroundColor DarkYellow }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Out = $out; Err = $err }
    } finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $stdout, $stderr
    }
}

function Get-WslDistros {
    $result = Invoke-Wsl -Arguments @('-l', '-q') -Quiet
    if ($result.ExitCode -ne 0) { return @() }
    return @($result.Out -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Test-Feature {
    param([string]$Name)
    try {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $Name -ErrorAction Stop
        return $feature.State -eq 'Enabled'
    } catch {
        return $null   # la característica no existe en esta imagen
    }
}

# ---------------------------------------------------------------------------
# 1. requisitos
# ---------------------------------------------------------------------------

Write-Host "Astra WebUI — instalación en WSL" -ForegroundColor White
Write-Step '1. Requisitos de Windows'

if (-not (Test-Admin)) {
    Write-Warn2 'no se está ejecutando como administrador; algunas acciones pueden fallar'
}

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) { Write-Bad 'no existe wsl.exe (WSL no está instalado)'; exit 1 }
Write-Ok "wsl.exe encontrado: $($wsl.Source)"

$wslFeature = Test-Feature 'Microsoft-Windows-Subsystem-Linux'
if ($wslFeature -eq $true) { Write-Ok 'característica "Subsistema de Windows para Linux" habilitada' }
elseif ($wslFeature -eq $false) { Write-Bad 'la característica WSL está deshabilitada: dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all' }
else { Write-Bad 'la característica WSL no existe en esta imagen de Windows' }

$vmFeature = Test-Feature 'VirtualMachinePlatform'
if ($vmFeature -eq $true) {
    Write-Ok 'característica "Plataforma de máquina virtual" (WSL2) habilitada'
} elseif ($vmFeature -eq $false) {
    Write-Bad 'VirtualMachinePlatform está deshabilitada: dism /online /enable-feature /featurename:VirtualMachinePlatform /all (y reinicia)'
} else {
    Write-Bad 'VirtualMachinePlatform NO EXISTE en esta imagen de Windows: WSL2 no se puede activar'
    Write-Host @'
Esto ocurre cuando el almacén de componentes de Windows está incompleto.
Para repararlo hace falta un medio de instalación de la MISMA versión:
  dism /online /cleanup-image /restorehealth /source:D:\sources\install.wim /index:1 /limitaccess
  dism /online /enable-feature /featurename:VirtualMachinePlatform /all
  reiniciar, y después: wsl --update
'@ -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------------
# 2. distribución
# ---------------------------------------------------------------------------

Write-Step "2. Distribución WSL ($Distro)"
$distros = Get-WslDistros
if ($distros.Count -gt 0) { Write-Info ("instaladas: " + ($distros -join ', ')) } else { Write-Info 'no hay ninguna distribución instalada' }

if ($distros -notcontains $Distro) {
    if ($Check) {
        Write-Info "se instalaría $Distro con: wsl --install -d $Distro"
    } elseif ($InstallDistro) {
        if ($vmFeature -ne $true) {
            Write-Bad "no se puede instalar ${Distro}: falta la plataforma de máquina virtual (ver arriba)"
        } elseif ($PSCmdlet.ShouldProcess($Distro, 'wsl --install')) {
            Write-Info "instalando $Distro (puede tardar y pedir reiniciar)…"
            $install = Invoke-Wsl -Arguments @('--install', '-d', $Distro, '--no-launch')
            if ($install.ExitCode -ne 0) {
                $install = Invoke-Wsl -Arguments @('--install', '-d', $Distro)
            }
            if ($install.ExitCode -ne 0) { Write-Bad "la instalación de $Distro falló; reinicia Windows y vuelve a intentarlo" }
            else { Write-Ok "$Distro instalada" }
            $distros = Get-WslDistros
        }
    } else {
        Write-Bad "la distribución '$Distro' no está instalada (usa -InstallDistro o: wsl --install -d $Distro)"
    }
} else {
    Write-Ok "la distribución '$Distro' está instalada"
}

if ($Check) {
    Write-Host "`nModo comprobación: no se ha cambiado nada." -ForegroundColor Yellow
    if ($script:Problems -gt 0) { exit 1 } else { exit 0 }
}

if ($distros -notcontains $Distro) {
    Write-Host "`nNo se puede continuar sin una distribución WSL." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# 3. copiar el proyecto dentro de WSL
# ---------------------------------------------------------------------------

Write-Step '3. Copiando el proyecto a WSL'

$ProjectDir = (Resolve-Path -Path $ProjectDir).Path
if (-not (Test-Path (Join-Path $ProjectDir 'packaging/wsl/setup-in-wsl.sh'))) {
    Write-Bad "$ProjectDir no parece el proyecto astra-webui (falta packaging/wsl/setup-in-wsl.sh)"
    exit 1
}
Write-Info "origen: $ProjectDir"

$wslPathResult = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'wslpath', '-u', $ProjectDir) -Quiet
$wslPath = ''
if ($wslPathResult.ExitCode -eq 0 -and $wslPathResult.Out) {
    $wslPath = $wslPathResult.Out.Trim()
} else {
    # conversión manual: C:\a\b → /mnt/c/a/b
    $wslPath = '/mnt/' + ($ProjectDir.Substring(0, 1).ToLower()) + ($ProjectDir.Substring(2).Replace('\', '/'))
    Write-Info 'wslpath no disponible: se usa la conversión manual'
}
Write-Info "destino dentro de WSL: $wslPath"

if ($PSCmdlet.ShouldProcess('/root/astra-webui', 'copiar el proyecto')) {
    $copy = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'bash', '-lc',
        "set -e; rm -rf /root/astra-webui; mkdir -p /root/astra-webui; cp -a '$wslPath/.' /root/astra-webui/; chmod +x /root/astra-webui/packaging/build-deb.sh /root/astra-webui/packaging/wsl/setup-in-wsl.sh; ls /root/astra-webui | head -20")
    if ($copy.ExitCode -ne 0) { Write-Bad 'no se pudo copiar el proyecto dentro de WSL'; exit 1 }
    Write-Ok 'proyecto copiado en /root/astra-webui'
}

# ---------------------------------------------------------------------------
# 4. construir, instalar y probar dentro de WSL
# ---------------------------------------------------------------------------

Write-Step '4. Instalación dentro de WSL (compila el núcleo y monta el .deb)'
$setupArgs = 'bash /root/astra-webui/packaging/wsl/setup-in-wsl.sh --project /root/astra-webui'
if ($SkipDeps) { $setupArgs += ' --skip-deps' }

if ($PSCmdlet.ShouldProcess($Distro, 'ejecutar setup-in-wsl.sh')) {
    $setup = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'bash', '-lc', $setupArgs)
    if ($setup.ExitCode -ne 0) {
        Write-Bad 'la instalación dentro de WSL falló (mira la salida anterior)'
    } else {
        Write-Ok 'instalación completada dentro de WSL'
    }
}

# ---------------------------------------------------------------------------
# 5. datos finales
# ---------------------------------------------------------------------------

Write-Step '5. Acceso al panel'
$ipResult = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'bash', '-lc',
    "hostname -I | awk '{print `$1}'") -Quiet
$wslIp = if ($ipResult.Out) { $ipResult.Out.Trim() } else { '' }
$passResult = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--', 'bash', '-lc',
    'cat /etc/astra-webui/initial-admin-password 2>/dev/null') -Quiet
$password = if ($passResult.Out) { $passResult.Out.Trim() } else { '' }

Write-Info "IP de la distribución WSL: $($wslIp -replace '\s', ', ')"
if ($password) { Write-Info "contraseña inicial de admin: $password" }
else { Write-Info 'contraseña inicial: mira /etc/astra-webui/initial-admin-password dentro de WSL' }

$lanIp = $wslIp -replace '\s.*$', ''
if ($ExposeToLan -and $lanIp) {
    Write-Step '6. Exponiendo el panel a Windows/LAN'
    if ($PSCmdlet.ShouldProcess('portproxy', 'publicar el panel')) {
        & netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0 2>$null | Out-Null
        & netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=$lanIp | Out-Null
        & netsh interface portproxy delete v4tov4 listenport=8001 listenaddress=0.0.0.0 2>$null | Out-Null
        & netsh interface portproxy add v4tov4 listenport=8001 listenaddress=0.0.0.0 connectport=8001 connectaddress=$lanIp | Out-Null
        if (-not (Get-NetFirewallRule -DisplayName 'Astra WebUI' -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName 'Astra WebUI' -Direction Inbound -Protocol TCP `
                -LocalPort 8000, 8001 -Action Allow | Out-Null
        }
        Write-Ok "panel disponible en http://localhost:8000 (y desde la LAN en el puerto 8000)"
    }
}

if ($AutoStart) {
    Write-Step '7. Arranque automático al iniciar Windows'
    $taskName = 'AstraWebUI'
    $inner = 'systemctl start astra astra-webui 2>/dev/null || bash /root/astra-webui/packaging/wsl/setup-in-wsl.sh --no-deps --no-demo'
    $taskArg = '-d ' + $Distro + ' -u root -- bash -lc "' + $inner + '"'
    $action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument $taskArg
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    if ($PSCmdlet.ShouldProcess($taskName, 'crear tarea programada')) {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
            -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
        Write-Ok "tarea programada '$taskName' creada (arranca astra y el panel al iniciar Windows)"
    }
}

Write-Host @"

Resumen
-------
  Panel:      http://localhost:8000        (dentro de WSL: http://${lanIp}:8000)
  Usuario:    admin
  Contraseña: $password
  Streaming:  http://localhost:8001/live/<token>/<canal>.ts

  Entrar en WSL:      wsl -d $Distro
  Ver logs:           wsl -d $Distro -u root -- tail -f /var/log/astra/astra.log
  Estado servicios:   wsl -d $Distro -u root -- systemctl status astra astra-webui
  Desinstalar todo:   wsl --unregister $Distro   (borra la distribución entera)

"@ -ForegroundColor Gray

if ($script:Problems -gt 0) {
    Write-Host "$($script:Problems) problema(s) detectado(s)." -ForegroundColor Red
    exit 1
}
Write-Host 'Todo listo.' -ForegroundColor Green
exit 0
