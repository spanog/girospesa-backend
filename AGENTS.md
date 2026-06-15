# Backend agent notes

## Testing Guardrails

- Every backend change must update the closest test layer first: unit/service for pure logic, integration for real DB/API contracts.
- Stable JSON responses touched by the change should gain or update contract snapshots under `tests/__snapshots__/` or `tests/integration/__snapshots__/`.
- Normalize unstable values before snapshot comparison: UUID, token, timestamp, variable URL host/query.
- Supabase schema or RLS changes must keep `supabase db advisors --local` clean for touched areas; wrap `auth.uid()` / `auth.jwt()` as `select` expressions in policies when possible to avoid advisor performance warnings.
- Snapshot tests support, not replace, explicit assertions on permissions, ordering, lifecycle transitions, and domain invariants.
- Keep notification flows aligned across transports: `favorite_offer` logic must stay shared between the `/push/notify-favorites` webhook path and any local/development fallback executed during flyer publication.
- `favorite_offer` should follow anti-spam semantics: aggregate multiple matches from the same flyer into one notification per `user + flyer`, updating the existing row/push payload instead of inserting one card per matched product.
- Run:
  - `.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance`
  - `.venv/bin/python -m pytest tests/integration -v`
