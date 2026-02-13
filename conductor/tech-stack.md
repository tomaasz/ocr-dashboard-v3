# Tech Stack

## Backend
- **Language**: Python 3.x
- **Framework**: FastAPI (inferred from `app/main.py` and `routes/`)
- **Server**: Uvicorn (implied for FastAPI) or similar WSGI/ASGI server.
- **Database**: PostgreSQL (referenced by `OCR_PG_DSN`)

## Frontend
- **Templating**: Jinja2 (likely, given `templates/` directory)
- **Styling**: Custom CSS (`static/dashboard_v2.css`)
- **JavaScript**: Likely vanilla JS or lightweight libraries for the dashboard interactions.

## Infrastructure & DevOps
- **Service Management**: systemd (Linux), Task Scheduler (Windows)
- **Containerization/Virtualization**: Virtualenv (`venv`)
- **CI/CD**: GitHub Actions (referenced `scripts` for auto-sync, push retry)
- **Code Quality**: SonarQube (optional local instance)

## Automation
- **Browser Automation**: Playwright (referenced in `src/ocr_engine/ocr/engine/playwright_engine.py`) or Selenium (referenced `browser_controller.py` might imply this, but Playwright is explicitly named).
- **OCR Engine**: Custom OCR engine implementation in `src/ocr_engine`.
