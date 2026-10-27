$pipes = [System.IO.Directory]::GetFiles('\\.\pipe\')
$pipes | Where-Object { $_ -match 'Everything|Clone' } | Select-Object -First 10
Write-Output ('--- total pipes: ' + $pipes.Count)
