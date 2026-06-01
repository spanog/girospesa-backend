ALTER TABLE public.list_members
  DROP CONSTRAINT IF EXISTS list_members_invited_by_fkey;

ALTER TABLE public.list_members
  ADD CONSTRAINT list_members_invited_by_fkey
  FOREIGN KEY (invited_by)
  REFERENCES auth.users(id)
  ON DELETE SET NULL;

ALTER TABLE public.list_invites
  DROP CONSTRAINT IF EXISTS list_invites_invited_by_fkey;

ALTER TABLE public.list_invites
  ADD CONSTRAINT list_invites_invited_by_fkey
  FOREIGN KEY (invited_by)
  REFERENCES auth.users(id)
  ON DELETE CASCADE;

ALTER TABLE public.list_invites
  DROP CONSTRAINT IF EXISTS list_invites_accepted_by_fkey;

ALTER TABLE public.list_invites
  ADD CONSTRAINT list_invites_accepted_by_fkey
  FOREIGN KEY (accepted_by)
  REFERENCES auth.users(id)
  ON DELETE SET NULL;
