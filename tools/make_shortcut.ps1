$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop 'Everything Clone.lnk'
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = 'C:\workspace\zcode-workspace\everything-rea-clone\client\node_modules\electron\dist\electron.exe'
$lnk.Arguments = '.'
$lnk.WorkingDirectory = 'C:\workspace\zcode-workspace\everything-rea-clone\client'
$lnk.IconLocation = 'C:\workspace\zcode-workspace\everything-rea-clone\client\assets\icon.ico, 0'
$lnk.Description = 'Everything Clone (REA) desktop client'
$lnk.WindowStyle = 1
$lnk.Save()
$check = $ws.CreateShortcut($lnkPath)
Write-Output ('target   : ' + $check.TargetPath)
Write-Output ('args     : ' + $check.Arguments)
Write-Output ('workdir  : ' + $check.WorkingDirectory)
Write-Output ('icon     : ' + $check.IconLocation)
Write-Output ('target exists: ' + (Test-Path $check.TargetPath))
