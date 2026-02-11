#!/bin/bash

# --- KONFIGURACJA ---
DB_HOST="127.0.0.1"
DB_USER="tomaasz"
DB_NAME="ocr"
# Password should be in PGPASSWORD environment variable or set below
export PGPASSWORD="${PGPASSWORD:-123Karinka!@#}"

echo "🔄 Rozpoczynam aktualizację liczników plików..."

# Count total folders first
TOTAL=$(psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT COUNT(DISTINCT source_path) FROM ocr_raw_texts")
CURRENT=0

echo "📊 Znaleziono $TOTAL folderów do przetworzenia"
echo ""

# Pobieramy ścieżki
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT DISTINCT source_path FROM ocr_raw_texts" | while read -r p; do
    
    # Usuwamy znaki powrotu karetki (\r)
    clean_path=$(echo "$p" | sed 's/\r//g')
    
    # Pomijamy puste linie
    [[ -z "$clean_path" ]] && continue
    
    ((CURRENT++))

    if [ -d "$clean_path" ]; then
        # Liczymy tylko pliki w danym folderze
        COUNT=$(find "$clean_path" -maxdepth 1 -type f \
            ! -name 'Thumbs.db' ! -name '.DS_Store' | wc -l)
        
        echo "[$CURRENT/$TOTAL] 📁 $clean_path → $COUNT plików"

        # Aktualizacja tabeli w bazie danych (bezpośrednie połączenie)
        psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
        "INSERT INTO folder_file_counts (source_path, file_count, last_updated) \
         VALUES ('$clean_path', $COUNT, NOW()) \
         ON CONFLICT (source_path) DO UPDATE SET file_count = $COUNT, last_updated = NOW();" > /dev/null 2>&1

        # Aktualizacja listy plików w folderze (dla kolejki skanów)
        tmp_files=$(mktemp)
        find "$clean_path" -maxdepth 1 -type f \
            ! -name 'Thumbs.db' ! -name '.DS_Store' \
            -printf '%f\t%p\t%T@\n' > "$tmp_files"

        # Wyczyść poprzednie wpisy dla folderu
        escaped_path=${clean_path//\'/\'\'}
        psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
        "DELETE FROM folder_file_entries WHERE source_path = '$escaped_path';" > /dev/null 2>&1

        # Wstaw aktualne pliki (jeśli są)
        if [ -s "$tmp_files" ]; then
            awk -F '\t' -v sp="$clean_path" 'BEGIN{OFS="\t"} {print sp, $1, $2, $3}' "$tmp_files" | \
            psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
            "\copy folder_file_entries (source_path, file_name, full_path, mtime_epoch) FROM STDIN WITH (FORMAT text, DELIMITER E'\t')" > /dev/null 2>&1
        fi

        rm -f "$tmp_files"
    else
        # Jeśli ścieżka zaczyna się od "psql:", to znaczy, że psql wywalił błąd zamiast ścieżek
        if [[ "$clean_path" == psql:* ]]; then
            echo "❌ Błąd bazy danych: $clean_path"
        else
            echo "⚠️  [$CURRENT/$TOTAL] Ścieżka nie istnieje: $clean_path"
        fi
    fi
done

echo ""
echo "✅ Aktualizacja zakończona!"
