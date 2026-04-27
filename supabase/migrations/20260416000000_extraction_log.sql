-- extraction_log: structured log of AI extraction pipeline events.
-- Used for debugging failed or low-quality flyer extractions.

CREATE TABLE public.extraction_log (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    flyer_id uuid,
    supermarket_id uuid,
    supermarket_name text,
    -- 'success' | 'error' | 'warning' | 'info'
    event_type text NOT NULL,
    message text NOT NULL,
    -- Extra context: page index, retry count, elapsed seconds, raw error, etc.
    details jsonb,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT extraction_log_pkey PRIMARY KEY (id),
    CONSTRAINT extraction_log_flyer_id_fkey
        FOREIGN KEY (flyer_id) REFERENCES public.flyers(id) ON DELETE SET NULL
);

CREATE INDEX idx_extraction_log_flyer_id   ON public.extraction_log (flyer_id);
CREATE INDEX idx_extraction_log_event_type ON public.extraction_log (event_type);
CREATE INDEX idx_extraction_log_created_at ON public.extraction_log (created_at DESC);
