-- Migration 010: Notify on new source_path inserts
-- Creates a helper table and trigger to emit NOTIFY when a new source_path appears

CREATE TABLE IF NOT EXISTS public.source_path_seen (
    source_path TEXT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION public.notify_new_source_path()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.source_path_seen (source_path)
    VALUES (NEW.source_path)
    ON CONFLICT DO NOTHING;

    IF FOUND THEN
        PERFORM pg_notify('ocr_new_source_path', NEW.source_path);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_new_source_path ON public.ocr_raw_texts;
CREATE TRIGGER trg_notify_new_source_path
AFTER INSERT ON public.ocr_raw_texts
FOR EACH ROW
EXECUTE FUNCTION public.notify_new_source_path();
