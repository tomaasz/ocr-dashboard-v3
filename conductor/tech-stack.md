# Technology Stack

## Backend
- **Language**: Python 3.10+
- **Framework**: FastAPI (Asynchronous web framework)
- **Web Server**: Uvicorn (ASGI server)
- **Validation**: Pydantic v2 (Data validation and settings management)
- **Templates**: Jinja2 (Server-side rendering)

## Database
- **Engine**: PostgreSQL
- **Driver**: psycopg2-binary
- **Schema Management**: SQL migrations (lokalizowane w `scripts/migrations/`)

## OCR & Processing
- **Automation**: Playwright (Browser automation for OCR tasks)
- **AI Models**: Google Generative AI (Gemini) for advanced OCR/Analysis
- **Image Processing**: OpenCV (headless) and Pillow (PIL)
- **Source Access**: Tailscale / NAS (Remote filesystem integration)

## Infrastructure & Automation
- **OS Support**: Linux/WSL and Windows
- **Process Management**: Systemd (Services and Timers for automation)
- **Task Scheduling**: Bash scripts (Auto-sync, push-retry, reminders)
- **IP/Proxy Management**: Webshare IP/Proxy sync scripts

## Development & Quality
- **Linting & Formatting**: Ruff (Extremely fast Python linter and formatter)
- **Testing**: Pytest (with pytest-asyncio for async tests)
- **Code Quality**: SonarQube (Local instance on `http://localhost:9000`)
- **Environment**: Virtualenv (venv), .env files for configuration
