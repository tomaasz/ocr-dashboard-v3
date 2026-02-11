# OCR Dashboard V2 - Windows Setup Configuration Example
# =======================================================
# Edit this file and run before executing setup_windows.ps1

$Config = @{
    # GitHub Repository
    GitHubRepo     = "https://github.com/YOUR_USERNAME/ocr-dashboard-v3.git"
    
    # Installation Path
    InstallDir     = "C:\Dev\ocr-dashboard-v3"
    
    # NAS Configuration
    NASHost        = "192.168.1.100"      # NAS IP or hostname
    NASShare       = "scans"             # Share name on NAS
    NASDriveLetter = "Z:"          # Drive letter to use
    NASUsername    = ""               # NAS username
    NASPassword    = ""               # NAS password
    NASDomain      = "WORKGROUP"        # Windows domain (if applicable)
    
    # WSL Configuration
    InstallWSL     = $false            # Set to $true to install WSL
    WSLDistro      = "Ubuntu-24.04"     # WSL distribution name
}

# Export to environment for script to use
$env:NAS_HOST = $Config.NASHost
$env:NAS_SHARE = $Config.NASShare
$env:NAS_USERNAME = $Config.NASUsername
$env:NAS_PASSWORD = $Config.NASPassword
$env:NAS_DOMAIN = $Config.NASDomain

# Usage:
# 1. Copy this file: Copy-Item setup_config.example.ps1 my_setup_config.ps1
# 2. Edit your copy: notepad my_setup_config.ps1
# 3. Run: . .\my_setup_config.ps1; .\setup_windows.ps1
