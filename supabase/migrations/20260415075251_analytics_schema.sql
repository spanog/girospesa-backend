CREATE TABLE public.analytics_data (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    week_start date NOT NULL,
    metric_type text NOT NULL,
    category text,
    supermarket_id uuid,
    value double precision,
    description text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT analytics_data_pkey PRIMARY KEY (id)
);

ALTER TABLE public.analytics_data ADD CONSTRAINT analytics_data_supermarket_id_fkey FOREIGN KEY (supermarket_id) REFERENCES public.supermarkets(id);

CREATE INDEX idx_analytics_data_week_metric ON public.analytics_data (week_start, metric_type);