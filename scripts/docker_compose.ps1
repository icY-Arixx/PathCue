param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
)

$taskDockerBin = "C:\Users\AceXb\AppData\Local\Programs\DockerDesktop\resources\bin"
$env:Path = "$taskDockerBin;$env:Path"
& "$taskDockerBin\docker.exe" compose @ComposeArgs
exit $LASTEXITCODE
