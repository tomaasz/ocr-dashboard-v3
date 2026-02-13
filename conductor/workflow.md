# Workflow & Processes

## Development Workflow
1. **Repository Structure**: Single working copy per host.
2. **Sync Strategy**:
   - **Linux**: `scripts/auto_sync_runtime.sh` runs via systemd to pull changes from `origin/main`.
   - **Windows**: Task Scheduler equivalent.
3. **Commit Policy**:
   - Work in `ocr-dashboard-v3`.
   - Commit locally.
   - Push to GitHub.
   - Auto-sync updates runtime hosts.

## Operational Procedures
- **Restart**: `sudo systemctl restart ocr-dashboard.service`
- **Logs**: `journalctl -u ocr-dashboard.service -n 100 --no-pager`
- **Health Check**: `curl -I http://127.0.0.1:9090/`

## Configuration Management
- **Remote Hosts**: Configured via UI at `/#settings`.
- **Storage**: `~/.cache/ocr-dashboard-v3/remote_hosts.json`.
- **Environment Variables**: Fallback mechanism (see `docs/CONFIGURATION.md`).
