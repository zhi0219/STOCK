$gitCombined = [string]::Concat([string]$gitStdout, [string]$gitStderr)
$gitCombined = $gitCombined.Trim()
