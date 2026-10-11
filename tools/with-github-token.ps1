# Ejecuta un comando de Python con GITHUB_TOKEN leído del Administrador de
# credenciales de Windows (la credencial que guarda GitHub Desktop).
#
#   powershell -File tools/with-github-token.ps1 tools/github_push.py --branch X --dir Y
#
# No muestra nunca el token. Útil para publicar sin git ni gh.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Argv)

$ErrorActionPreference = 'Stop'
$sig = @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct CREDENTIAL {
    public uint Flags; public uint Type; public IntPtr TargetName; public IntPtr Comment;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
    public uint CredentialBlobSize; public IntPtr CredentialBlob; public uint Persist;
    public uint AttributeCount; public IntPtr Attributes; public IntPtr TargetAlias; public IntPtr UserName;
}
public static class CredHelperGh {
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CredReadW(string target, uint type, uint flags, out IntPtr credential);
    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern void CredFree(IntPtr buffer);
}
'@
Add-Type -TypeDefinition $sig -Language CSharp | Out-Null

function Get-GitHubToken {
    param([string]$Target = 'GitHub - https://api.github.com/TalaveraSama')
    $ptr = [IntPtr]::Zero
    if (-not [CredHelperGh]::CredReadW($Target, 1, 0, [ref]$ptr)) { return '' }
    try {
        $cred = [Runtime.InteropServices.Marshal]::PtrToStructure($ptr, [type][CREDENTIAL])
        $bytes = New-Object byte[] $cred.CredentialBlobSize
        [Runtime.InteropServices.Marshal]::Copy($cred.CredentialBlob, $bytes, 0, $cred.CredentialBlobSize)
    } finally { [CredHelperGh]::CredFree($ptr) }
    foreach ($encoding in @([Text.Encoding]::UTF8, [Text.Encoding]::Unicode)) {
        $candidate = ($encoding.GetString($bytes)).Trim([char]0, "`r", "`n", ' ')
        if ($candidate -match '^gh[pousr]_[A-Za-z0-9]{20,}$') { return $candidate }
    }
    return ''
}

$token = Get-GitHubToken
if (-not $token) {
    # alternativa: el token que guarda el propio git para github.com
    $token = Get-GitHubToken -Target 'git:https://github.com'
}
if (-not $token) { Write-Error 'no se encontró ninguna credencial de GitHub utilizable'; exit 1 }

Write-Host "[token] credencial encontrada (longitud $($token.Length))"
$env:GITHUB_TOKEN = $token
$env:PYTHONIOENCODING = 'utf-8'
& python @Argv
$code = $LASTEXITCODE
Remove-Item Env:\GITHUB_TOKEN -ErrorAction SilentlyContinue
exit $code
