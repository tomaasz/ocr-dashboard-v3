# Initial Concept
Samodzielny dashboard do zarządzania farmą OCR (OCR Farm), umożliwiający monitorowanie hostów, konfigurowanie profili przetwarzania oraz automatyzację cyklu pracy z dokumentami genealogicznymi.

# Product Definition

## Vision
Stworzenie centralnego punktu kontroli nad rozproszonym systemem OCR, który łączy tradycyjne techniki przetwarzania obrazu z nowoczesnymi modelami AI (Gemini), zapewniając użytkownikowi pełną przejrzystość i kontrolę nad procesem digitalizacji zasobów archiwalnych.

## Target Users
- **Administratorzy Systemu**: Osoby odpowiedzialne za infrastrukturę, monitorujące stan zdrowia hostów (farm health) i zapewniające ciągłość działania usług.
- **Badacze i Genealodzy**: Główni użytkownicy dashboardu, konfigurujący parametry OCR dla konkretnych zespołów archiwalnych i nadzorujący postępy prac.
- **Deweloperzy**: Osoby rozwijające silnik OCR, potrzebujące wglądu w logi, błędy i wydajność poszczególnych modeli AI.

## Core Features
- **Zarządzanie Hostami**: Podgląd stanu zdalnych hostów, ich obciążenia i dostępności.
- **Konfiguracja Profili**: Elastyczne definiowanie ścieżek źródłowych (NAS Tailscale), liczby workerów i parametrów silnika OCR.
- **Monitorowanie Przetwarzania**: Śledzenie postępu w czasie rzeczywistym, licznik przetworzonych stron i obsługa błędów.
- **Integracja z AI**: Obsługa wielu silników OCR, w tym zaawansowanego modelu Gemini AI do analizy trudnych dokumentów.
- **Automatyzacja**: Systemy auto-sync, przypomnienia o commitach i automatyczne sprzątanie artefaktów.

## Success Metrics
- Stabilne działanie dashboardu na wielu platformach (Linux/WSL, Windows).
- Minimalizacja czasu potrzebnego na konfigurację nowych zadań OCR.
- Wysoka skuteczność rozpoznawania tekstu przy optymalnym wykorzystaniu zasobów AI.
