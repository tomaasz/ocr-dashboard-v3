# Product Definition

## Overview
OCR Dashboard V2 is a standalone dashboard for managing an OCR Farm. It coordinates OCR workers, manages Chrome profiles for browser automation, and provides a UI for monitoring and configuration.

## Key Features
- **OCR Farm Management**: Manage distributed OCR workers.
- **Browser Automation**: Uses Chrome profiles for automated OCR tasks.
- **Monitoring UI**: Web-based dashboard for real-time status (Port 9090).
- **Remote Host Configuration**: Centralized configuration for remote workers.
- **Global Source Directory**: Unified source path management (via NAS).

## Architecture
- **Single Repo**: Designed for a single working copy per host, kept in sync with `origin/main`.
- **Systemd Integration**: Uses systemd timers for auto-sync and commit reminders on Linux/Ubuntu.
- **Task Scheduler**: Windows support via Task Scheduler.

## Access
- **URL**: `http://localhost:9090/v2`
- **Settings**: `/#settings`
