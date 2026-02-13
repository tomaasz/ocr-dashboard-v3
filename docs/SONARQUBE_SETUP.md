# Instrukcja Wdrożenia SonarQube (Self-Hosted)

Twoje środowisko: `http://54.37.252.57:9000`
Projekt: `tomaasz_ocr-dashboard-v3`

## 1. Konfiguracja Tokena (Wymagane!)

1. Zaloguj się do SonarQube.
2. Kliknij ikonę użytkownika (prawy górny róg) -> **My Account** -> **Security**.
3. W sekcji **Tokens** wpisz nazwę (np. "CI-Token") i kliknij **Generate**.
4. Skopiuj token.
5. Wklej go do pliku `.env` w twoim projekcie (na końcu pliku):
   ```bash
   SONAR_TOKEN=twoj_wygenerowany_token
   ```

## 2. Uruchamianie Analizy (Local / Manual)

Aby uruchomić pełen cykl (testy + coverage + scan):

```bash
make sonar
```

Co to robi:

1. Uruchamia `pytest` z generowaniem `coverage.xml`.
2. Naprawia ścieżki w `coverage.xml` (jeśli były z dockera).
3. Wysyła dane do SonarQube.

## 3. Konfiguracja IDE (VS Code + SonarLint)

Wymagane rozszerzenie: **SonarLint for VS Code**

1. Otwórz ustawienia SonarLint w VS Code (`Ctrl+Shift+P` -> `SonarLint: Configure SonarQube Project Connection`).
2. **Server URL**: `http://54.37.252.57:9000`
3. **User Token**: Użyj tego samego tokena co w `.env` (lub wygeneruj osobny "IDE-Token").
4. **Project Key**: Wybierz z listy lub wpisz: `tomaasz_ocr-dashboard-v3`.

Teraz błędy z serwera będą widoczne w twoim edytorze w czasie rzeczywistym!

## 4. Konfiguracja CI (GitHub Actions)

Utwórz plik `.github/workflows/sonar.yml`:

```yaml
name: SonarQube Scan

on:
  push:
    branches: ["main", "develop"]
  pull_request:
    branches: ["main", "develop"]

jobs:
  sonar:
    name: Test and Scan
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # Ważne dla wykrywania nowych linii kodu

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest pytest-cov

      - name: Run Tests with Coverage
        run: |
          make test

      - name: SonarQube Scan
        uses: sonarsource/sonarqube-scan-action@master
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
          SONAR_HOST_URL: http://54.37.252.57:9000
```

> **Ważne**: W repozytorium GitHub (Settings -> Secrets) dodaj sekret `SONAR_TOKEN`.

## Checklist "Gotowe do produkcji"

- [ ] `.env` zawiera poprawny `SONAR_TOKEN`.
- [ ] `make sonar` przechodzi lokalnie bez błędów.
- [ ] Projekt w SonarQube pokazuje poprawne "Coverage %" (nie 0.0%).
- [ ] SonarLint w IDE jest połączony (zielona ikonka/brak błędów połączenia).
- [ ] Token nie jest wkommitowany do repozytorium (jest w `.env`, który jest w `.gitignore`).
