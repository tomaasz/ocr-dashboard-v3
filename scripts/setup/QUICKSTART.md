# Quick Setup Reference Card

## 🚀 Ubuntu Setup (3 commands)

```bash
cd ~/ocr-dashboard-v3/scripts/setup
export NAS_HOST=192.168.1.100 NAS_SHARE=scans NAS_USERNAME=user NAS_PASSWORD=pass GITHUB_REPO=https://github.com/YOUR_USER/ocr-dashboard-v3.git
./setup_ubuntu.sh
```

## 🚀 Arch Linux Setup (3 commands)

```bash
cd ~/ocr-dashboard-v3/scripts/setup
export NAS_HOST=192.168.1.100 NAS_SHARE=scans NAS_USERNAME=user NAS_PASSWORD=pass GITHUB_REPO=https://github.com/YOUR_USER/ocr-dashboard-v3.git
./setup_arch.sh
```

## 🚀 Windows Setup (Run as Admin)

```powershell
cd C:\path\to\ocr-dashboard-v3\scripts\setup
# Edit setup_windows.ps1 first (update $Config section)
PowerShell -ExecutionPolicy Bypass -File setup_windows.ps1
```

## 📦 What Gets Installed

- ✅ Git
- ✅ Python 3.10+
- ✅ PostgreSQL client libraries
- ✅ Chromium browser
- ✅ Python packages (FastAPI, Playwright, etc.)
- ✅ NAS mount configuration
- ✅ OCR Dashboard repository

## 🔧 Post-Installation

```bash
# Linux
cd ~/ocr-dashboard-v3
source venv/bin/activate
nano .env  # Edit configuration
./scripts/start_web.sh

# Windows
cd C:\Dev\ocr-dashboard-v3
.\venv\Scripts\Activate.ps1
notepad .env  # Edit configuration
python run.py
```

Access: http://localhost:9090/v2

## 🗂️ NAS Mount Locations

- **Linux**: `/mnt/nas-scans`
- **Windows**: `Z:\`
- **WSL**: `/mnt/z` or `drvfs` mount

## ⚙️ Key Configuration Variables

| Variable           | Purpose          | Example                           |
| ------------------ | ---------------- | --------------------------------- |
| `GITHUB_REPO`      | Your repo URL    | `https://github.com/you/repo.git` |
| `NAS_HOST`         | NAS server IP    | `192.168.1.100`                   |
| `NAS_SHARE`        | Share name       | `scans`                           |
| `NAS_USERNAME`     | NAS user         | `admin`                           |
| `NAS_PASSWORD`     | NAS password     | `secret`                          |
| `INSTALL_POSTGRES` | Install local DB | `yes` or `no`                     |

## 🛠️ Troubleshooting

**NAS won't mount?**

```bash
# Linux - Test manually
sudo mount -t cifs //192.168.1.100/scans /mnt/test -o username=user,password=pass

# Windows - Test manually
net use Z: \\192.168.1.100\scans /user:user pass
```

**Python issues?**

```bash
# Linux
python3 --version  # Must be 3.10+
sudo apt install python3-venv build-essential

# Windows
python --version  # Must be 3.10+
# Reinstall Python from python.org if needed
```

**Playwright browser fails?**

```bash
# Linux
playwright install-deps chromium

# Windows
# Run in PowerShell as Admin
playwright install chromium
```

## 📚 Full Documentation

See [README.md](README.md) for complete documentation.
