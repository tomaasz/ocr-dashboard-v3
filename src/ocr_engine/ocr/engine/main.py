# main.py
import os
import sys

from engine import GeminiWebEngineV2  # Importujemy Twoją klasę z pliku engine.py


def main():
    # Konfiguracja katalogów (możesz zmienić lub zostawić domyślne z klasy)
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Czy tryb z oknem (headed)?
    # Ustaw na True przy pierwszym uruchomieniu, żeby się zalogować!
    is_headed = os.environ.get("OCR_HEADED", "0") == "1"

    engine = GeminiWebEngineV2(
        job_dir=current_dir,
        headed=is_headed,  # True = widać przeglądarkę, False = w tle
        enable_video=False,  # Możesz włączyć nagrywanie wideo dla debugowania
        enable_trace=False,
    )

    print(f"--- URUCHAMIANIE OCR (Profil: {os.environ.get('OCR_PROFILE_SUFFIX', 'domyślny')}) ---")
    exit_code = engine.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
