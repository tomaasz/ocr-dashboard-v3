# Implementation Plan: Farm Health Monitoring & OCR Error Optimization

## Phase 1: Foundation and Enhanced Logging
- [ ] Task: Przygotowanie środowiska testowego dla usług monitorowania
    - [ ] Napisanie testów jednostkowych dla nowych metryk w `monitor_farm_health.py`
    - [ ] Implementacja metryk dostępności NAS i API Gemini
- [ ] Task: Rozbudowa schematu bazy danych dla logowania błędów
    - [ ] Przygotowanie migracji dodającej kolumny metadanych do `error_traces`
    - [ ] Napisanie testów integracyjnych dla zapisu rozszerzonych śladów błędów
    - [ ] Implementacja logiki zapisu metadanych w `error_handlers.py`
- [ ] Task: Conductor - User Manual Verification 'Phase 1' (Protocol in workflow.md)

## Phase 2: OCR Engine Error Optimization
- [ ] Task: Ulepszenie mechanizmu przechwytywania błędów w silniku OCR
    - [ ] Napisanie testów symulujących awarie Playwright i Gemini
    - [ ] Optymalizacja `src/ocr_engine/ocr/engine/base.py` pod kątem granulacji wyjątków
- [ ] Task: Zarządzanie artefaktami po awarii
    - [ ] Napisanie testów dla `app/services/cleanup.py` w scenariuszach błędów
    - [ ] Implementacja logiki selektywnego zachowywania logów/obrazów przy krytycznych błędach
- [ ] Task: Conductor - User Manual Verification 'Phase 2' (Protocol in workflow.md)

## Phase 3: Dashboard Integration
- [ ] Task: Prezentacja rozszerzonych statusów zdrowia w UI
    - [ ] Testy renderowania nowych metryk w szablonach Jinja2
    - [ ] Aktualizacja `templates/dashboard_v2.html` o widok szczegółowy hosta
- [ ] Task: Widok szczegółowy błędów
    - [ ] Implementacja modalu/strony z pełnym śladem błędu z `error_traces`
- [ ] Task: Conductor - User Manual Verification 'Phase 3' (Protocol in workflow.md)
