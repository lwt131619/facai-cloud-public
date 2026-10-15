# Verify (never assume) that Facai Cloud is publicly reachable over HTTPS.
# Run from Windows:  .\deploy\check_public_access.ps1 -Domain whstapp.lol -ServerIp 77.221.148.118
# Set $env:FACAI_ADMIN_PASSWORD to probe authenticated access; it is never printed.
[CmdletBinding()]
param(
    [string]$Domain = 'whstapp.lol',
    [string]$ServerIp = '77.221.148.118',
    [string]$AdminUser = 'admin'
)
$ErrorActionPreference = 'Continue'
$fail = 0
function Check([bool]$ok, [string]$message) {
    if ($ok) { Write-Host "PASS: $message" } else { Write-Host "FAIL: $message"; $script:fail = 1 }
}

Write-Host '== DNS (resolver cache, informational) =='
try {
    $a = (Resolve-DnsName -Name $Domain -Type A -ErrorAction Stop | Where-Object { $_.Section -eq 'Answer' -and $_.IPAddress }).IPAddress
    $www = (Resolve-DnsName -Name "www.$Domain" -Type A -ErrorAction Stop | Where-Object { $_.Section -eq 'Answer' -and $_.IPAddress }).IPAddress
    Write-Host "A $Domain -> $($a -join ', ')"
    Write-Host "A www.$Domain -> $($www -join ', ')"
} catch {
    Write-Host "resolver lookup for $Domain failed: $($_.Exception.Message)"
}

# A cached resolver can show the new address while a NameSilo/dnsowl
# authoritative server still answers with an old one (or vice versa). The gate
# must therefore require EVERY authoritative answer to be exactly $ServerIp.
function Test-OnlyServer([string[]]$ips) {
    if (-not $ips -or $ips.Count -eq 0) { return $false }
    $diff = Compare-Object -ReferenceObject @($ServerIp) -DifferenceObject $ips
    return ($null -eq $diff)
}

Write-Host '== DNS (authoritative dnsowl) =='
foreach ($ns in 'ns1.dnsowl.com', 'ns2.dnsowl.com', 'ns3.dnsowl.com') {
    try {
        $a = @(Resolve-DnsName -Name $Domain -Type A -Server $ns -DnsOnly -ErrorAction Stop |
            Where-Object { $_.Type -eq 'A' -and $_.Section -eq 'Answer' } |
            ForEach-Object { $_.IPAddress } | Sort-Object -Unique)
        $w = @(Resolve-DnsName -Name "www.$Domain" -Type A -Server $ns -DnsOnly -ErrorAction Stop |
            Where-Object { $_.Type -eq 'A' -and $_.Section -eq 'Answer' } |
            ForEach-Object { $_.IPAddress } | Sort-Object -Unique)
        Write-Host "  @$ns A $Domain -> $(if ($a) { $a -join ', ' } else { '<none>' })"
        Write-Host "  @$ns A www.$Domain -> $(if ($w) { $w -join ', ' } else { '<none>' })"
        Check (Test-OnlyServer $a) "authoritative $ns apex is exactly $ServerIp"
        Check (Test-OnlyServer $w) "authoritative $ns www is exactly $ServerIp"
    } catch {
        Check $false "authoritative lookup @$ns failed: $($_.Exception.Message)"
    }
}

Write-Host '== TCP =='
$tcp = Test-NetConnection -ComputerName $ServerIp -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue
Check $tcp 'port 443 reachable'

Write-Host '== TLS =='
try {
    $client = New-Object System.Net.Sockets.TcpClient($Domain, 443)
    $ssl = New-Object System.Net.Security.SslStream($client.GetStream(), $false, { $true })
    $ssl.AuthenticateAsClient($Domain)
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
    Write-Host "subject=$($cert.Subject) expires=$($cert.NotAfter)"
    Check ($cert.NotAfter -gt (Get-Date)) 'TLS certificate is valid'
    $ssl.Close(); $client.Close()
} catch {
    Check $false "TLS handshake failed: $($_.Exception.Message)"
}

Write-Host '== HTTP =='
$base = "https://$Domain"
try {
    $health = Invoke-WebRequest -Uri "$base/api/v1/health" -UseBasicParsing -TimeoutSec 15
    Check ($health.StatusCode -eq 200) 'health 200'
} catch {
    Check $false "health request failed: $($_.Exception.Message)"
}
try {
    Invoke-WebRequest -Uri "$base/api/v1/missions" -UseBasicParsing -TimeoutSec 15 | Out-Null
    Check $false 'anonymous API access returned 200 (should be blocked)'
} catch {
    $code = [int]$_.Exception.Response.StatusCode
    Check ($code -in 401, 403, 303) "anonymous API access blocked ($code)"
}
if ($env:FACAI_ADMIN_PASSWORD) {
    try {
        $pair = "$AdminUser`:$env:FACAI_ADMIN_PASSWORD"
        $basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
        $auth = Invoke-WebRequest -Uri "$base/api/v1/missions" -Headers @{ Authorization = "Basic $basic" } -UseBasicParsing -TimeoutSec 15
        Check ($auth.StatusCode -eq 200) 'authenticated API access works'
    } catch {
        Check $false "authenticated request failed: $($_.Exception.Message)"
    }
} else {
    Write-Host 'SKIP: set $env:FACAI_ADMIN_PASSWORD to probe authenticated access'
}

Write-Host '== Summary =='
if ($fail -eq 0) { Write-Host "PUBLIC ACCESS VERIFIED for $Domain" }
else { Write-Host 'NOT LIVE: one or more checks failed. Do not claim public availability.' }
exit $fail
