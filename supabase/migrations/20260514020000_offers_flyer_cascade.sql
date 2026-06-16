-- Change offers.flyer_id FK from ON DELETE SET NULL to ON DELETE CASCADE
-- Deleting a flyer now hard-deletes all linked offers (draft and confirmed).
ALTER TABLE offers
  DROP CONSTRAINT IF EXISTS offers_flyer_id_fkey;

ALTER TABLE offers
  ADD CONSTRAINT offers_flyer_id_fkey
    FOREIGN KEY (flyer_id)
    REFERENCES flyers(id)
    ON DELETE CASCADE;
