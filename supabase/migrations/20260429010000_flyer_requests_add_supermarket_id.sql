-- Migration: add supermarket_id FK to flyer_requests
-- Enables counting requests per supermarket branch without string grouping.

alter table flyer_requests
  add column supermarket_id uuid references supermarkets(id) on delete set null;
