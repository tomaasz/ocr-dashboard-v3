# Specification: Farm Health Monitoring & OCR Error Optimization

## Overview
Celem tego tracka jest poprawa stabilności i obserwowalności systemu OCR poprzez rozbudowę mechanizmów monitorowania zdrowia hostów oraz bardziej precyzyjne przechwytywanie i raportowanie błędów w silniku OCR.

## Objectives
- Rozszerzenie skryptu `scripts/monitor_farm_health.py` o dodatkowe metryki (np. dostępność NAS, czas odpowiedzi API Gemini).
- Ulepszenie tabeli `system_activity_log` oraz `error_traces` o bardziej szczegółowe metadane.
- Optymalizacja `app/services/cleanup.py` w celu lepszego zarządzania artefaktami po błędach.
- Poprawa widoczności błędów w dashboardzie (templates/dashboard_v2.html).

## Technical Requirements
- Wszystkie zmiany muszą być zgodne z TDD (testy przed implementacją).
- Pokrycie testami dla nowych funkcjonalności >80%.
- Wykorzystanie istniejących mechanizmów logowania (`log_utils.py`).
- Bezpieczeństwo ścieżek (`path_security.py`).
