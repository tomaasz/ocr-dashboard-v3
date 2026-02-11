# OCR Dashboard V2 - Host Setup Scripts

This directory contains automated setup scripts for quickly configuring new hosts to run OCR workers in your OCR Dashboard V2 infrastructure.

## Available Scripts

### 🐧 Ubuntu (`setup_ubuntu.sh`)

For Ubuntu 20.04+ servers and workstations.

### 🔷 Arch Linux (`setup_arch.sh`)

For Arch Linux and Arch-based distributions (Manjaro, EndeavourOS, etc.).

### 🪟 Windows 11 (`setup_windows.ps1`)

For Windows 11 hosts with optional WSL integration.

## Quick Start

### Ubuntu / Arch Linux

```bash
# 1. Configure the script (edit variables at the top)
nano scripts/setup/setup_ubuntu.sh  # or setup_arch.sh

# 2. Set environment variables (optional)
export GITHUB_REPO="https://github.com/YOUR_USERNAME/ocr-dashboard-v3.git"
export NAS_HOST="192.168.1.100"
export NAS_SHARE="scans"
export NAS_USERNAME="your_username"
export NAS_PASSWORD="your_password"
export INSTALL_POSTGRES="yes"  # Optional: install local PostgreSQL

# 3. Run the setup script
chmod +x scripts/setup/setup_ubuntu.sh
./scripts/setup/setup_ubuntu.sh
```

### Windows 11

```powershell
# 1. Configure the script (edit $Config hashtable at the top)
notepad scripts\setup\setup_windows.ps1

# 2. Run as Administrator
PowerShell -ExecutionPolicy Bypass -File scripts\setup\setup_windows.ps1
```

## What These Scripts Do

All scripts perform the following tasks automatically:

1. **Install System Dependencies**
   - Git
   - Python 3.10+ and pip
   - Required system libraries
   - Chromium/Chrome browser
   - PostgreSQL client libraries

2. **Mount NAS Folder**
   - Creates mount point (Linux) or network drive (Windows)
   - Configures persistent mounting via `/etc/fstab` (Linux) or persistent network drive (Windows)
   - Sets up credentials securely

3. **Clone Repository**
   - Clones your OCR Dashboard V2 repository from GitHub
   - Installs to standard locations:
     - Linux: `~/ocr-dashboard-v3`
     - Windows: `C:\Dev\ocr-dashboard-v3`

4. **Setup Python Environment**
   - Creates Python virtual environment
   - Installs all required Python packages from `requirements.txt`
   - Installs Playwright browsers (Chromium)

5. **Configure Environment**
   - Creates `.env` file with sensible defaults
   - Pre-configures paths for NAS mount, repo directory, etc.

6. **Optional: Auto-Start Service**
   - Linux: systemd service
   - Windows: Task Scheduler job

## Configuration Variables

### Common Variables (All Platforms)

| Variable       | Description                    | Default                                                             |
| -------------- | ------------------------------ | ------------------------------------------------------------------- |
| `GITHUB_REPO`  | Your GitHub repository URL     | (must be set)                                                       |
| `INSTALL_DIR`  | Installation directory         | `~/ocr-dashboard-v3` (Linux) or `C:\Dev\ocr-dashboard-v3` (Windows) |
| `NAS_HOST`     | NAS server hostname or IP      | `192.168.1.100`                                                     |
| `NAS_SHARE`    | NAS share name                 | `scans`                                                             |
| `NAS_USERNAME` | NAS username                   | (empty)                                                             |
| `NAS_PASSWORD` | NAS password                   | (empty)                                                             |
| `NAS_DOMAIN`   | Windows domain (if applicable) | `WORKGROUP`                                                         |

### Linux-Specific Variables

| Variable           | Description                     | Default          |
| ------------------ | ------------------------------- | ---------------- |
| `NAS_MOUNT_POINT`  | Local mount point for NAS       | `/mnt/nas-scans` |
| `INSTALL_POSTGRES` | Install local PostgreSQL server | `no`             |
| `PG_USER`          | PostgreSQL username             | `ocr_user`       |
| `PG_PASSWORD`      | PostgreSQL password             | `ocr_password`   |
| `PG_DATABASE`      | PostgreSQL database name        | `ocr_db`         |

### Windows-Specific Variables

| Variable         | Description                 | Default        |
| ---------------- | --------------------------- | -------------- |
| `NASDriveLetter` | Drive letter for NAS mount  | `Z:`           |
| `InstallWSL`     | Install WSL Ubuntu          | `$false`       |
| `WSLDistro`      | WSL distribution to install | `Ubuntu-24.04` |

## NAS Mounting Details

### Linux (Ubuntu/Arch)

The scripts use `cifs-utils` to mount Windows/Samba shares. The mount configuration is added to `/etc/fstab` for persistence across reboots.

**Credentials Storage**: Stored securely in `/etc/nas-credentials` with 600 permissions (root-only read).

**Manual Mount** (if credentials not provided):

```bash
sudo mount -t cifs //NAS_HOST/SHARE /mnt/nas-scans -o username=USER,password=PASS
```

### Windows

The scripts use `net use` to map a network drive. The drive mapping is set to persistent.

**Manual Mount** (if credentials not provided):

```powershell
net use Z: \\NAS_HOST\SHARE /user:USERNAME PASSWORD /persistent:yes
```

### WSL Ubuntu (on Windows)

If you're using WSL, you can mount the Windows network drive inside WSL:

```bash
# In WSL terminal
sudo mkdir -p /mnt/nas-scans
sudo mount -t drvfs Z: /mnt/nas-scans
```

To make it persistent, add to `/etc/fstab` in WSL:

```
Z: /mnt/nas-scans drvfs defaults 0 0
```

## PostgreSQL Installation

### Ubuntu/Arch Linux

Set `INSTALL_POSTGRES=yes` to automatically install and configure a local PostgreSQL server.

The script will:

- Install PostgreSQL
- Create database and user
- Configure authentication
- Start and enable the service

**Connection String**: `postgresql://ocr_user:ocr_password@localhost:5432/ocr_db`

### Windows

PostgreSQL installation is not automated for Windows. Options:

1. **Use Remote Database**: Point `OCR_PG_DSN` to an existing PostgreSQL server
2. **Install Manually**: Download from [postgresql.org](https://www.postgresql.org/download/windows/)
3. **Use Docker**: Run PostgreSQL in a Docker container

## Post-Installation Steps

After running the setup script:

1. **Edit `.env` File**

   ```bash
   cd ~/ocr-dashboard-v3  # or C:\Dev\ocr-dashboard-v3
   nano .env              # or notepad .env
   ```

   Update:
   - `OCR_PG_DSN` - PostgreSQL connection string
   - `OCR_REMOTE_HOST` - Remote worker host (if applicable)
   - Other configuration as needed

2. **Start the Dashboard**

   Linux:

   ```bash
   cd ~/ocr-dashboard-v3
   source venv/bin/activate
   ./scripts/start_web.sh
   ```

   Windows:

   ```powershell
   cd C:\Dev\ocr-dashboard-v3
   .\venv\Scripts\Activate.ps1
   python run.py
   ```

3. **Access the Dashboard**

   Open browser to: `http://localhost:9090/v2`

4. **Configure Remote Hosts** (if needed)

   Go to `http://localhost:9090/#settings` to configure remote worker hosts

## Systemd Service (Linux)

If you chose to install the systemd service:

```bash
# Start service
sudo systemctl start ocr-dashboard

# Check status
sudo systemctl status ocr-dashboard

# View logs
journalctl -u ocr-dashboard -f

# Stop service
sudo systemctl stop ocr-dashboard

# Disable auto-start
sudo systemctl disable ocr-dashboard
```

## Task Scheduler (Windows)

If you chose to install the Task Scheduler job:

```powershell
# Start task manually
Start-ScheduledTask -TaskName "OCR Dashboard V2"

# Check status
Get-ScheduledTask -TaskName "OCR Dashboard V2" | Select-Object State

# Disable auto-start
Disable-ScheduledTask -TaskName "OCR Dashboard V2"

# Remove task
Unregister-ScheduledTask -TaskName "OCR Dashboard V2" -Confirm:$false
```

## Troubleshooting

### Common Issues

#### NAS Mount Fails

**Symptoms**: Mount command fails or mount point is empty

**Solutions**:

- Verify NAS is accessible: `ping NAS_HOST`
- Check credentials are correct
- Check share name is correct
- On Linux, check `/var/log/syslog` for mount errors
- On Windows, try mounting manually first to test credentials

#### Python Dependencies Fail to Install

**Symptoms**: `pip install` errors during setup

**Solutions**:

- Ensure internet connection is working
- Check Python version: `python --version` (must be 3.10+)
- On Linux, ensure build tools are installed: `sudo apt install build-essential libpq-dev`
- Try manual install: `source venv/bin/activate && pip install -r requirements.txt -v`

#### Playwright Browser Installation Fails

**Symptoms**: `playwright install` fails or browser doesn't launch

**Solutions**:

- Run with system dependencies: `playwright install-deps chromium`
- On Linux, ensure X11 or Wayland is available (or use headless mode)
- Check disk space (browsers are ~500MB)

#### Service Won't Start

**Symptoms**: systemd service or Task Scheduler job fails

**Solutions**:

- Check logs: `journalctl -u ocr-dashboard -n 50` (Linux)
- Verify `.env` file exists and is valid
- Check PostgreSQL is running: `systemctl status postgresql`
- Test manual start first: `cd ~/ocr-dashboard-v3 && source venv/bin/activate && python run.py`

## Security Notes

1. **Credentials Storage**
   - Linux: NAS credentials in `/etc/nas-credentials` (root-only, 600 permissions)
   - Windows: Credentials stored in Windows Credential Manager
   - Never commit `.env` file to Git

2. **PostgreSQL**
   - Default passwords are for development only
   - Change passwords in production: `ALTER USER ocr_user PASSWORD 'new_password';`
   - Configure firewall to restrict PostgreSQL access

3. **File Permissions**
   - Scripts should NOT be run as root (except for mount operations)
   - Virtual environment is user-owned
   - Service runs as your user account

## Customization

You can customize the scripts by editing the configuration section at the top:

```bash
# Ubuntu/Arch example
GITHUB_REPO="https://github.com/YOUR_ORG/ocr-dashboard-v3.git"
INSTALL_DIR="/opt/ocr-dashboard"
NAS_MOUNT_POINT="/data/scans"
INSTALL_POSTGRES="yes"
```

```powershell
# Windows example
$Config = @{
    GitHubRepo = "https://github.com/YOUR_ORG/ocr-dashboard-v3.git"
    InstallDir = "D:\Applications\ocr-dashboard-v3"
    NASDriveLetter = "S:"
    InstallWSL = $true
}
```

## Uninstallation

To remove the installation:

### Linux

```bash
# Stop and remove service (if installed)
sudo systemctl stop ocr-dashboard
sudo systemctl disable ocr-dashboard
sudo rm /etc/systemd/system/ocr-dashboard.service
sudo systemctl daemon-reload

# Unmount NAS
sudo umount /mnt/nas-scans
# Remove from /etc/fstab (edit manually)

# Remove installation
rm -rf ~/ocr-dashboard-v3
rm -rf ~/.cache/ocr-dashboard-v3

# Optional: Remove PostgreSQL
sudo systemctl stop postgresql
sudo apt remove --purge postgresql postgresql-contrib  # Ubuntu
sudo pacman -Rns postgresql  # Arch
```

### Windows

```powershell
# Remove scheduled task (if installed)
Unregister-ScheduledTask -TaskName "OCR Dashboard V2" -Confirm:$false

# Disconnect NAS drive
net use Z: /delete /yes

# Remove installation
Remove-Item -Path C:\Dev\ocr-dashboard-v3 -Recurse -Force
Remove-Item -Path $env:LOCALAPPDATA\ocr-dashboard-v3 -Recurse -Force -ErrorAction SilentlyContinue
```

## Support

For issues or questions:

- Check the main [README.md](../../README.md)
- Review [docs/CONFIGURATION.md](../../docs/CONFIGURATION.md)
- Open an issue on GitHub

## License

Same as OCR Dashboard V2 main project.
