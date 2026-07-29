# Security Assessment — 29 July 2026

## Scope and evidence

The assessment covered the local FastAPI, Next.js, Supabase schema and Storage policy source, Capacitor configuration, GitHub Actions, dependency lockfiles, and the isolated local Supabase integration stack. No production or staging endpoint was contacted. No real account, notification, email, Gemini extraction, or production data was used.

Validated identities and controls include guest, authenticated user, list owner/member/outsider, supermarket manager, and admin. The local integration suite confirmed list RLS isolation, hidden private RPC helpers, manager supermarket scoping, invite ownership, and notification ownership.

## Findings and remediation status

| ID | Severity | Finding | Status |
| --- | --- | --- | --- |
| SEC-01 | Medium | API JWT verification accepted any audience and issuer when the signature was valid. | Fixed: FastAPI now requires the Supabase Auth issuer and `authenticated` audience. |
| SEC-02 | Medium | Flyer and draft-image validation trusted the client-declared MIME type. | Fixed: PDF, JPEG, PNG, WebP, and GIF magic bytes are checked before persistence; Storage paths derive extensions from validated types. |
| SEC-05 | High | Signed flyer uploads are limited only after the API downloads the object; a privileged uploader could consume backend memory with an oversized object. | Open: enforce a 50 MB object limit in managed Storage or at an upload gateway before staging credentials are issued. |
| SEC-06 | Low | `ecdsa` has an upstream timing advisory without an available package fix. | Accepted temporarily: the app does not call `SigningKey.sign_digest()` and uses it only transitively through JWT verification. Review on the next `python-jose` release. |

## Controls and validation

- Weekly Dependabot updates plus dependency and secret scans are configured.
- Backend unit suite: 461 passed. Backend integration suite: 25 passed.
- `pip-audit -r requirements.txt`: one accepted residual finding (`ecdsa`, SEC-06).

## Required staging follow-up

Use synthetic guest, customer A/B, manager A/B, and admin accounts to validate deployed configuration. Confirm a pre-upload Storage size limit for SEC-05, Auth redirect allow-list and rate limits, Supabase security advisors, environment access, branch protection, and alerting. Do not run brute-force or availability tests.
