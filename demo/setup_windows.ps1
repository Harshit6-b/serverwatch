# One-time setup on the Windows laptop. Run in an Administrator PowerShell from the serverwatch folder.
python -m pip install -r agent\requirements.txt
# Let the beacon and other PCs reach the dashboard on port 8080
New-NetFirewallRule -DisplayName "ServerWatch agent 8080" -Direction Inbound -Protocol TCP `
    -LocalPort 8080 -Action Allow -Profile Any -ErrorAction SilentlyContinue | Out-Null
# Quick WMI sanity check (Experiment 5, Listing 4.1)
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, LoadPercentage
"Setup done. Start the agent with:  python agent\serverwatch.py --services Spooler"
