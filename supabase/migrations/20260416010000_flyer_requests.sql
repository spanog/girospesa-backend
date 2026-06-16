-- Migration: create flyer_requests table
-- Users (guest or authenticated) can submit requests for flyers not yet
-- covered by the app. The backend notifies the admin via email.

create table if not exists flyer_requests (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  city        text not null,
  supermarket text,               -- null = "all supermarkets in this city"
  flyer_url   text,               -- optional direct link to the flyer
  notes       text check (char_length(notes) <= 500),
  email       text,               -- optional user email for reply
  user_id     uuid references auth.users(id) on delete set null,
  status      text not null default 'pending'
                check (status in ('pending', 'reviewed', 'done'))
);

-- RLS: INSERT open to everyone (authenticated + anonymous); SELECT/UPDATE restricted to service_role
alter table flyer_requests enable row level security;

create policy "Anyone can insert flyer requests"
  on flyer_requests for insert
  with check (true);

-- No SELECT / UPDATE policy → only service_role (bypasses RLS) can read/update rows
