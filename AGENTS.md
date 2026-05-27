# Backend agent notes

## Testing Guardrails

- Every backend change must update the closest test layer first: unit/service for pure logic, integration for real DB/API contracts.
- Stable JSON responses touched by the change should gain or update contract snapshots under `tests/__snapshots__/` or `tests/integration/__snapshots__/`.
- Normalize unstable values before snapshot comparison: UUID, token, timestamp, variable URL host/query.
- Snapshot tests support, not replace, explicit assertions on permissions, ordering, lifecycle transitions, and domain invariants.
- Run:
  - `.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance`
  - `.venv/bin/python -m pytest tests/integration -v`
