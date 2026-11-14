#Requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$BundleRoot)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# This deliberately changes only an ephemeral GitHub-hosted CI account and
# freshly built test directories. It is never part of normal application startup.
if ($env:CI -ne 'true' -or $env:GITHUB_ACTIONS -ne 'true') {
    throw 'This ordinary-user test helper may run only in GitHub Actions CI.'
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This CI test requires Windows.'
}
if (-not $env:GITHUB_WORKSPACE -or -not [IO.Path]::IsPathRooted($BundleRoot)) {
    throw 'An absolute bundle path inside GITHUB_WORKSPACE/build-portable is required.'
}
$workspace = [IO.Path]::GetFullPath($env:GITHUB_WORKSPACE)
$buildParent = [IO.Path]::GetFullPath((Join-Path $workspace 'build-portable'))
$allowedRoot = $buildParent + [IO.Path]::DirectorySeparatorChar
$bundle = [IO.Path]::GetFullPath($BundleRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
if (-not $bundle.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $bundle -PathType Container)) {
    throw 'Unsafe CI bundle path: it must be an existing child of workspace/build-portable.'
}
if (Test-Path -LiteralPath (Join-Path $bundle 'data')) {
    throw 'The CI bundle must be fresh and contain no user data.'
}
if ((Get-Item -LiteralPath $bundle).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'A CI bundle must not be a directory link.'
}
if ((Get-Item -LiteralPath $buildParent).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'The fresh CI build directory must not be a directory link.'
}
if (Get-ChildItem -LiteralPath $bundle -Recurse -Force -Attributes ReparsePoint | Select-Object -First 1) {
    throw 'A CI bundle must not contain directory links or other reparse points.'
}
$python = Join-Path $bundle 'runtime/python/python.exe'
$test = Join-Path $bundle 'portable/test_bundle.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
    -not (Test-Path -LiteralPath $test -PathType Leaf)) {
    throw 'The bundled Python interpreter or integration test is missing.'
}

function ConvertTo-WindowsArgument {
    param([AllowEmptyString()][string]$Value)
    # Start-Process joins ArgumentList verbatim. Quote for the Windows C-runtime
    # parser, including embedded quotes and backslashes before a closing quote.
    $quoted = $Value -replace '(\\*)"', '$1$1\"'
    $quoted = $quoted -replace '(\\+)$', '$1$1'
    return '"' + $quoted + '"'
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isElevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 10)
$username = 'ci_photo_' + $suffix
$logDirectory = Join-Path $allowedRoot ('ci-integration-' + $suffix)
New-Item -ItemType Directory -Path $logDirectory | Out-Null
# Keep the original archive input pristine and place the relocation test wholly
# inside one private CI directory, without granting rights on its parent.
$testBundle = Join-Path $logDirectory 'test-bundle'
Copy-Item -LiteralPath $bundle -Destination $testBundle -Recurse
$test = Join-Path $testBundle 'portable/test_bundle.py'
# The integration process must not map Python/DLL files from the directory it
# renames. Use a private copy of the same bundled interpreter outside that tree.
$harnessRuntime = Join-Path $logDirectory 'harness-python'
Copy-Item -LiteralPath (Join-Path $bundle 'runtime/python') -Destination $harnessRuntime -Recurse
$harnessPython = Join-Path $harnessRuntime 'python.exe'
if (-not $isElevated) {
    Write-Output 'The CI process is already unprivileged; running the native test directly.'
    Push-Location -LiteralPath $logDirectory
    try {
        & $harnessPython -I -B $test --bundle $testBundle
        if ($LASTEXITCODE -ne 0) { throw "Native integration failed (exit $LASTEXITCODE)." }
    } finally {
        Pop-Location
    }
    return
}
$stdout = Join-Path $logDirectory 'stdout.log'
$stderr = Join-Path $logDirectory 'stderr.log'
$bytes = New-Object byte[] 32
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
$password = 'Ci!' + [Convert]::ToBase64String($bytes) + 'a9'
Write-Output "::add-mask::$password"
$securePassword = ConvertTo-SecureString $password -AsPlainText -Force
$account = $null

try {
    $account = New-LocalUser -Name $username -Password $securePassword -AccountNeverExpires `
        -PasswordNeverExpires -UserMayNotChangePassword -Description 'Temporary offline application CI test'
    $users = Get-LocalGroup -SID 'S-1-5-32-545'
    if (-not (Get-LocalGroupMember -Group $users.Name | Where-Object { $_.SID.Value -eq $account.SID.Value })) {
        Add-LocalGroupMember -Group $users.Name -Member $account
    }
    $sid = $account.SID.Value
    # PostgreSQL removes administrator SIDs. Grant only this temporary ordinary
    # user rights on the private CI directory, including its test-bundle copy.
    & "$env:SystemRoot\System32\icacls.exe" $logDirectory /grant "*$($sid):(OI)(CI)F" /T /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not grant access to the fresh CI test directory.' }
    $credential = New-Object Management.Automation.PSCredential(([Environment]::MachineName + '\' + $username), $securePassword)
    $arguments = @('-I', '-B', $test, '--bundle', $testBundle)
    $argumentLine = ($arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '
    Write-Output 'Running offline startup and relocation as a temporary standard Windows user.'
    # Keep both the harness's working directory and redirection handles outside
    # the bundle so its relocation test can rename the complete directory.
    $process = Start-Process -FilePath $harnessPython -ArgumentList $argumentLine -WorkingDirectory $logDirectory `
        -Credential $credential -LoadUserProfile -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Ordinary-user native integration failed (exit $($process.ExitCode))." }
    Write-Output 'Ordinary-user native integration passed.'
} finally {
    try {
        foreach ($log in @($stdout, $stderr)) {
            if (Test-Path -LiteralPath $log) {
                Write-Output (Split-Path -Leaf $log)
                Get-Content -LiteralPath $log -Encoding UTF8
            }
        }
    } finally {
        try {
            if ($null -ne $account) {
                Remove-LocalUser -SID $account.SID
            }
        } finally {
            $securePassword.Dispose()
            $password = $null
        }
    }
}
