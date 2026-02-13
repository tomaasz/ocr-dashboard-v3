# Product Guidelines

## Core Principles
- **Accuracy First**: W procesie OCR dokładność rozpoznawania tekstu jest ważniejsza niż szybkość. Każdy błąd powinien być logowany i możliwy do prześledzenia.
- **Resilience**: System musi być odporny na przerwy w połączeniu sieciowym (szczególnie z NAS i Tailscale) oraz limity API modeli AI.
- **Transparency**: Użytkownik musi zawsze wiedzieć, co dzieje się w systemie – od stanu hostów po szczegółowe błędy konkretnego dokumentu.

## Prose Style & Tone
- **Technical & Direct**: Używamy precyzyjnej terminologii technicznej. Komunikaty powinny być krótkie, rzeczowe i pozbawione zbędnych upiększeń.
- **Action-Oriented**: Instrukcje i opisy funkcji powinny jasno wskazywać na oczekiwany rezultat lub wymagane działanie użytkownika.
- **Consistent Terminology**: Konsekwentnie używamy terminów takich jak "Host", "Profile", "Worker", "Source Path", "Dashing", aby uniknąć nieporozumień.

## Design & UI Guidelines
- **Dashboard Efficiency**: Interfejs (Jinja2/CSS) powinien priorytetyzować gęstość informacji nad pustą przestrzenią, umożliwiając szybki przegląd wielu hostów jednocześnie.
- **Visual Feedback**: Każda zmiana stanu (np. start workera, błąd skanowania) musi być wyraźnie sygnalizowana kolorystyką (zgodnie z dashboard_v2.css).
- **Responsive Layout**: Dashboard musi zachować pełną funkcjonalność na systemach Linux/WSL oraz Windows, zgodnie z założeniami projektu.

## Code & Quality Standards
- **Standardized Logging**: Wszystkie usługi muszą korzystać z `log_utils.py` i zapewniać czytelne logi systemowe dostępne przez dashboard.
- **Security First**: Ścieżki do plików muszą być walidowane przez `path_security.py`, a wrażliwe dane (DSN, klucze API) zarządzane przez zmienne środowiskowe lub bezpieczną konfigurację.
- **Test-Driven Reliability**: Nowe funkcje powinny być pokryte testami (pytest), szczególnie w obszarze usług biznesowych i integracji z bazą danych.
