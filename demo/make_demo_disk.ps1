# Creates a 1 GB virtual disk mounted as S: so the disk-full demo does not touch C:.
# Run in an Administrator PowerShell.  Remove it later with remove_demo_disk.ps1
$vhd = "$env:SystemDrive\serverwatch-demo.vhdx"
$cmds = @"
create vdisk file="$vhd" maximum=1024 type=expandable
select vdisk file="$vhd"
attach vdisk
create partition primary
format fs=ntfs quick label=SWDEMO
assign letter=S
"@
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value $cmds -Encoding ASCII
diskpart /s $tmp
Remove-Item $tmp
Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='S:'" | Select-Object DeviceID, DriveType, Size, FreeSpace
