# Crea un acceso directo "AutoRed" en el Escritorio que abre el lanzador sin ventana de consola.
$py = (& py -3.12 -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2>$null)
if (-not $py -or -not (Test-Path $py.Trim())) { Write-Host "No encontre Python 3.12 (pythonw.exe). Instala Python 3.12 y vuelve a intentar."; exit 1 }
$ws = New-Object -ComObject WScript.Shell
$escritorio = [Environment]::GetFolderPath('Desktop')
$lnk = $ws.CreateShortcut("$escritorio\AutoRed.lnk")
$lnk.TargetPath = $py.Trim()
$lnk.Arguments = '"' + (Join-Path $PSScriptRoot 'AutoRed.pyw') + '"'
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.IconLocation = (Join-Path $PSScriptRoot 'autored.ico')
$lnk.Description = 'AutoRed - Automatizacion de redes para Cisco Packet Tracer'
$lnk.Save()
Write-Host "Listo: se creo el acceso directo AutoRed en $escritorio"
