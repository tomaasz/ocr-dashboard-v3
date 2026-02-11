# DB — schematy i użycie

W repo są **dwa niezależne przypadki użycia DB**:

1) **OCR Farm (produkcyjnie)**
2) **Queue pipeline job.json (stub)**

---

## 1) OCR Farm (produkcyjnie)

OCR Engine V2 zapisuje wyniki do tabeli wskazanej przez `OCR_PG_TABLE`
(domyślnie `public.ocr_raw_texts`) i tworzy tabelę blokad `public.ocr_locks`.

Minimalny rekomendowany schemat:

```sql
CREATE TABLE IF NOT EXISTS public.ocr_raw_texts (
  file_name TEXT NOT NULL,
  source_path TEXT NOT NULL,
  batch_id TEXT,
  raw_text TEXT,
  page_no INT,
  created_at TIMESTAMPTZ DEFAULT now(),
  ocr_duration_sec DOUBLE PRECISION,
  start_ts TIMESTAMPTZ,
  end_ts TIMESTAMPTZ,
  browser_profile TEXT,
  browser_id TEXT,
  model_label TEXT,
  card_id TEXT,
  PRIMARY KEY (source_path, file_name)
);
```

Tabela blokad (`public.ocr_locks`) jest tworzona automatycznie
przy `OCR_PG_ENABLED=1`.

---

## 2) Queue pipeline job.json (stub)

Plik `schema.sql` dotyczy stuba pipeline `job.json` i **nie jest używany**
przez produkcyjny runner `run.py`.

Zastosowanie:
```bash
psql "$DATABASE_URL" -f src/ocr_engine/db/schema.sql
```

Schemat zawiera tabele `jobs`, `job_entries`, `job_runs` i wspiera
`FOR UPDATE SKIP LOCKED`.

