# Detaches and deletes the demo disk made by make_demo_disk.ps1 (Administrator PowerShell).
$vhd = "$env:SystemDrive\serverwatch-demo.vhdx"
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value "select vdisk file=`"$vhd`"`r`ndetach vdisk" -Encoding ASCII
diskpart /s $tmp
Remove-Item $tmp
Remove-Item $vhd -ErrorAction SilentlyContinue
"demo disk removed"
