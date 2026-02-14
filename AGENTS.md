# AGENTS.md — Bootstrap for OCR Dashboard V3

## Project Overview

**OCR Dashboard V3** is a standalone web dashboard for managing a distributed OCR Farm. It enables monitoring of remote hosts, configuring processing profiles, and automating the digitization workflow for genealogical archival documents using AI-powered OCR (Google Gemini).

**Language:** Python 3.10+ (target: 3.12)
**Primary Framework:** FastAPI + Uvicorn (ASGI)

## Repository Structure

```
ocr-dashboard-v3/
├── app/                    # FastAPI web application
│   ├── main.py             # FastAPI entry point (app factory)
│   ├── config.py           # Configuration (env vars, defaults)
│   ├── routes/             # API endpoints (dashboard, settings, API)
│   ├── services/           # Business logic layer
│   ├── models/             # Pydantic models
│   └── utils/              # Helpers and utilities
├── src/
│   └── ocr_engine/         # OCR processing engine
│       └── ocr/engine/     # Gemini-based OCR engine (Playwright browser automation)
├── conductor/              # Project orchestration docs & guidelines
├── scripts/                # Automation, migrations, setup scripts
│   ├── migrations/         # PostgreSQL SQL migrations
│   ├── systemd/            # Systemd service/timer units
│   └── setup/              # Initial setup scripts
├── tests/                  # Pytest test suite
├── config/                 # Additional configuration files
├── run.py                  # OCR engine runner (CLI entry point)
├── .agent/                 # Agent skills, workflows, rules
│   ├── skills/             # Superpowers framework + custom skills
│   ├── workflows/          # Defined workflows (e.g., /create-issue)
│   └── rules/              # Always-on rules (Snyk security)
├── pyproject.toml          # Project metadata, Ruff config, Pytest config
└── requirements.txt        # Python dependencies
```

## Technology Stack

| Layer         | Technology                                   |
| ------------- | -------------------------------------------- |
| Web Framework | FastAPI + Uvicorn                            |
| Templates     | Jinja2 (server-side rendering)               |
| Database      | PostgreSQL (psycopg2-binary)                 |
| Validation    | Pydantic v2                                  |
| HTTP Client   | httpx                                        |
| OCR Engine    | Google Generative AI (Gemini) via Playwright |
| Image         | OpenCV (headless), Pillow                    |
| Network       | Tailscale (NAS access), Webshare (proxies)   |
| Linting       | Ruff (line-length=100, target=py312)         |
| Testing       | Pytest + pytest-asyncio (asyncio_mode=auto)  |
| Code Quality  | SonarQube (local), Snyk (security scanning)  |
| Process Mgmt  | Systemd services and timers                  |

## Key Entry Points

- **Web dashboard:** `app/main.py` → Start with `scripts/start_web.sh` or `OCR_DASHBOARD_PORT=9090 uvicorn app.main:app`
- **OCR engine:** `run.py` → Configurable via env vars: `OCR_BATCH_ID`, `OCR_HEADED`, `OCR_PROFILE_SUFFIX`
- **Migrations:** `scripts/run_migrations.py` → SQL files in `scripts/migrations/`

## Configuration

Configuration is via environment variables (`.env` file supported):

| Variable              | Description                  | Default |
| --------------------- | ---------------------------- | ------- |
| `OCR_PG_DSN`          | PostgreSQL connection string | —       |
| `OCR_REMOTE_HOST`     | Remote worker host           | —       |
| `OCR_DEFAULT_WORKERS` | Default workers per profile  | 2       |
| `OCR_DASHBOARD_PORT`  | Dashboard HTTP port          | 9090    |

Full configuration docs: `docs/CONFIGURATION.md`

## Coding Conventions

1. **Style:** Ruff enforces all linting and formatting. Run `ruff check .` and `ruff format .` before committing.
2. **Imports:** isort via Ruff. First-party package: `app`.
3. **Line length:** 100 characters max.
4. **Types:** Use Pydantic v2 models for validation. Use type hints everywhere.
5. **Async:** FastAPI routes are async. Use `pytest-asyncio` for async tests.
6. **Testing:** Tests in `tests/` directory. Run with `pytest`. Aim for ≥80% coverage.
7. **Security:** Snyk scans are mandatory for new code. Fix all issues before committing.
8. **Database:** PostgreSQL only. Migrations are sequential SQL files in `scripts/migrations/`.

## Development Workflow

```bash
# Setup
cd /home/tomaasz/ocr-dashboard-v3
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run web dashboard
OCR_DASHBOARD_PORT=9090 ./scripts/start_web.sh

# Run tests
pytest

# Lint & format
ruff check . --fix
ruff format .

# Run OCR engine
python run.py
```

## Remote Hosts & Source Files

- Remote hosts config is UI-editable at `/#settings`, stored in `~/.cache/ocr-dashboard-v3/remote_hosts.json`
- Global source directory on NAS (Tailscale): `tomaasz@kosciesza:~/Genealogy/Sources/`
- Profile paths are relative to the global base

## Agent Skills (Superpowers Framework)

The project uses the [Superpowers](https://github.com/obra/superpowers) framework installed in `.agent/skills/superpowers/`. Skills include:

- **brainstorming** — Socratic design refinement
- **writing-plans** — Implementation planning
- **executing-plans** — Batch execution
- **test-driven-development** — RED-GREEN-REFACTOR
- **systematic-debugging** — 4-phase root cause analysis
- **verification-before-completion** — Ensure fixes work

Skills are auto-discovered from `.agent/skills/` directory.

## Important Notes

- The OCR engine uses Playwright for browser automation with Gemini AI. It supports auto-restart on browser crashes (exit code 100).
- Proxy management is handled via Webshare scripts (`scripts/webshare_*.py`).
- Systemd units handle auto-sync, commit reminders, and push retries.
- The project runs on both Linux/WSL and Windows (paths differ per platform).
