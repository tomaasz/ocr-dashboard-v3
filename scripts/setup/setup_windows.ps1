# OCR Dashboard V2 - Windows 11 Host Setup Script
# ================================================
# This script configures a new Windows 11 host for running OCR workers
#
# Usage (Run as Administrator):
#   PowerShell -ExecutionPolicy Bypass -File setup_windows.ps1
#
# What it does:
# 1. Installs Chocolatey package manager (if needed)
# 2. Installs required software (Git, Python, WSL)
# 3. Mounts NAS folder with scans as network drive
# 4. Clones repository from GitHub
# 5. Sets up Python virtual environment
# 6. Installs Python dependencies
# 7. Installs Playwright browsers
# 8. Creates Windows Task Scheduler job (optional)

# Requires Administrator privileges
#Requires -RunAsAdministrator

# Configuration - Edit these values
$Config = @{
    GitHubRepo = "https://github.com/YOUR_USERNAME/ocr-dashboard-v3.git"
    InstallDir = "C:\Dev\ocr-dashboard-v3"
    NASHost = $env:NAS_HOST ?? "192.168.1.100"
    NASShare = $env:NAS_SHARE ?? "scans"
    NASDriveLetter = "Z:"
    NASUsername = $env:NAS_USERNAME ?? ""
    NASPassword = $env:NAS_PASSWORD ?? ""
    NASDomain = $env:NAS_DOMAIN ?? "WORKGROUP"
    InstallWSL = $false  # Set to $true to install WSL Ubuntu
    WSLDistro = "Ubuntu-24.04"
}

# Color output functions
function Write-ColorOutput {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Message,
        [string]$Color = "White"
    )
    Write-Host $Message -ForegroundColor $Color
}

function Write-Info { Write-ColorOutput -Message "[INFO] $args" -Color Cyan }
function Write-Success { Write-ColorOutput -Message "[SUCCESS] $args" -Color Green }
function Write-Warning { Write-ColorOutput -Message "[WARNING] $args" -Color Yellow }
function Write-Error { Write-ColorOutput -Message "[ERROR] $args" -Color Red }

function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Install-Chocolatey {
    Write-Info "Checking for Chocolatey..."
    
    if (Get-Command choco -ErrorAction SilentlyContinue) {
        Write-Success "Chocolatey already installed"
        return
    }
    
    Write-Info "Installing Chocolatey..."
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://chocolatey.org/install.ps1'))
    
    # Refresh environment
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    
    Write-Success "Chocolatey installed"
}

function Install-SystemPackages {
    Write-Info "Installing system packages..."
    
    # Install Git
    if (!(Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Info "Installing Git..."
        choco install git -y
    } else {
        Write-Success "Git already installed"
    }
    
    # Install Python
    if (!(Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Info "Installing Python..."
        choco install python311 -y
    } else {
        Write-Success "Python already installed"
    }
    
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    
    Write-Success "System packages installed"
}

function Install-WSL {
    if (!$Config.InstallWSL) {
        Write-Info "Skipping WSL installation (InstallWSL=false)"
        return
    }
    
    Write-Info "Installing WSL..."
    
    # Check if WSL is already installed
    $wslInstalled = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
    
    if ($wslInstalled.State -eq "Enabled") {
        Write-Success "WSL already installed"
    } else {
        Write-Info "Enabling WSL feature..."
        wsl --install --no-distribution
        Write-Warning "System restart may be required. Run this script again after restart."
        return
    }
    
    # Install Ubuntu distribution
    Write-Info "Installing $($Config.WSLDistro)..."
    wsl --install -d $Config.WSLDistro
    
    Write-Success "WSL and Ubuntu installed"
}

function Mount-NASShare {
    Write-Info "Mounting NAS share..."
    
    $driveLetter = $Config.NASDriveLetter
    $nasPath = "\\$($Config.NASHost)\$($Config.NASShare)"
    
    # Check if drive already mapped
    if (Test-Path $driveLetter) {
        Write-Warning "Drive $driveLetter already exists"
        
        $response = Read-Host "Remove and re-map? (y/N)"
        if ($response -eq 'y' -or $response -eq 'Y') {
            net use $driveLetter /delete /yes
        } else {
            Write-Info "Skipping NAS mount"
            return
        }
    }
    
    # Map network drive
    if ($Config.NASUsername -and $Config.NASPassword) {
        Write-Info "Mapping $nasPath to $driveLetter..."
        
        $username = if ($Config.NASDomain) { "$($Config.NASDomain)\$($Config.NASUsername)" } else { $Config.NASUsername }
        
        net use $driveLetter $nasPath /user:$username $Config.NASPassword /persistent:yes
        
        if (Test-Path $driveLetter) {
            Write-Success "NAS mounted successfully at $driveLetter"
        } else {
            Write-Error "Failed to mount NAS"
        }
    } else {
        Write-Warning "NAS credentials not provided - manual mounting required"
        Write-Info "Manual mount command:"
        Write-Info "net use $driveLetter $nasPath /user:USERNAME PASSWORD /persistent:yes"
    }
}

function Clone-Repository {
    Write-Info "Cloning repository from GitHub..."
    
    if (Test-Path $Config.InstallDir) {
        Write-Warning "Directory $($Config.InstallDir) already exists"
        
        $response = Read-Host "Remove and re-clone? (y/N)"
        if ($response -eq 'y' -or $response -eq 'Y') {
            Remove-Item -Path $Config.InstallDir -Recurse -Force
        } else {
            Write-Info "Skipping repository clone"
            return
        }
    }
    
    # Create parent directory if needed
    $parentDir = Split-Path -Parent $Config.InstallDir
    if (!(Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    
    git clone $Config.GitHubRepo $Config.InstallDir
    
    if (Test-Path $Config.InstallDir) {
        Write-Success "Repository cloned to $($Config.InstallDir)"
    } else {
        Write-Error "Failed to clone repository"
        exit 1
    }
}

function Setup-PythonEnvironment {
    Write-Info "Setting up Python virtual environment..."
    
    Set-Location $Config.InstallDir
    
    # Create virtual environment
    python -m venv venv
    
    # Activate virtual environment
    & .\venv\Scripts\Activate.ps1
    
    # Upgrade pip
    Write-Info "Upgrading pip..."
    python -m pip install --upgrade pip setuptools wheel
    
    # Install dependencies
    Write-Info "Installing Python dependencies..."
    pip install -r requirements.txt
    
    # Install Playwright browsers
    Write-Info "Installing Playwright browsers..."
    playwright install chromium
    playwright install-deps chromium
    
    Write-Success "Python environment configured"
}

function Create-EnvFile {
    Write-Info "Creating .env configuration file..."
    
    Set-Location $Config.InstallDir
    
    $envFile = ".env"
    if (Test-Path $envFile) {
        Write-Warning ".env file already exists - creating .env.new instead"
        $envFile = ".env.new"
    }
    
    $nasMountPoint = $Config.NASDriveLetter
    
    $envContent = @"
# OCR Dashboard V2 - Environment Configuration
OCR_PG_DSN=postgresql://user:password@localhost:5432/ocr_db

# Remote Worker Configuration
OCR_REMOTE_HOST=
OCR_REMOTE_USER=$env:USERNAME
OCR_REMOTE_REPO_DIR=$($Config.InstallDir -replace '\\', '/')
OCR_REMOTE_SOURCE_DIR=$nasMountPoint

# Remote Browser (Windows/WSL) Configuration
OCR_REMOTE_BROWSER_ENABLED=1
OCR_REMOTE_BROWSER_HOST=localhost
OCR_REMOTE_BROWSER_USER=$env:USERNAME
OCR_REMOTE_BROWSER_PROFILE_ROOT=C:\\Users\\$env:USERNAME\\AppData\\Local\\Google\\Chrome\\User Data
OCR_REMOTE_BROWSER_PYTHON=C:\\Python311\\python.exe
OCR_REMOTE_BROWSER_CHROME_BIN=C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe
OCR_REMOTE_BROWSER_RUNNER=wsl.exe
OCR_REMOTE_BROWSER_WSL_DISTRO=$($Config.WSLDistro)

# Dashboard Configuration  
OCR_DASHBOARD_PORT=9090
OCR_DEFAULT_WORKERS=2
OCR_DEFAULT_SCANS_PER_WORKER=2

# Update Counts
OCR_UPDATE_COUNTS_ON_START=1
OCR_UPDATE_COUNTS_MIN_INTERVAL_SEC=900
"@
    
    Set-Content -Path $envFile -Value $envContent
    
    Write-Success "Environment file created: $envFile"
    
    if ($envFile -eq ".env.new") {
        Write-Warning "Please review .env.new and merge with existing .env"
    }
}

function Setup-TaskScheduler {
    Write-Info "Setting up Windows Task Scheduler job..."
    
    $response = Read-Host "Install Task Scheduler job for auto-start? (y/N)"
    if ($response -ne 'y' -and $response -ne 'Y') {
        Write-Info "Skipping Task Scheduler job installation"
        return
    }
    
    $taskName = "OCR Dashboard V2"
    $pythonExe = Join-Path $Config.InstallDir "venv\Scripts\python.exe"
    $runScript = Join-Path $Config.InstallDir "run.py"
    
    # Remove existing task if present
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    
    # Create new task
    $action = New-ScheduledTaskAction -Execute $pythonExe -Argument $runScript -WorkingDirectory $Config.InstallDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings
    
    Write-Success "Task Scheduler job created: $taskName"
    Write-Info "The dashboard will start automatically on system boot"
}

function Print-Summary {
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Green
    Write-Success "OCR Dashboard V2 - Windows 11 Setup Complete!"
    Write-Host "==================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Installation directory: $($Config.InstallDir)"
    Write-Host "NAS drive letter: $($Config.NASDriveLetter)"
    Write-Host "Python virtual environment: $($Config.InstallDir)\venv"
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Review and edit $($Config.InstallDir)\.env"
    Write-Host "  2. Start the dashboard:"
    Write-Host "     cd $($Config.InstallDir)"
    Write-Host "     .\venv\Scripts\Activate.ps1"
    Write-Host "     python run.py"
    Write-Host "  3. Access at http://localhost:9090/v2"
    Write-Host ""
    Write-Host "For WSL integration, see docs for additional setup steps."
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Green
}

function Main {
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  OCR Dashboard V2 - Windows 11 Host Setup" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host ""
    
    if (!(Test-Administrator)) {
        Write-Error "This script must be run as Administrator"
        Write-Info "Right-click PowerShell and select 'Run as Administrator'"
        exit 1
    }
    
    Install-Chocolatey
    Install-SystemPackages
    Install-WSL
    Mount-NASShare
    Clone-Repository
    Setup-PythonEnvironment
    Create-EnvFile
    Setup-TaskScheduler
    Print-Summary
}

# Run the main function
Main
