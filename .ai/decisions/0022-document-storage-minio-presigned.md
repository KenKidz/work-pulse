# 22. Employee document storage: private MinIO + presigned two-step upload

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

`.ai/features/employee-documents.md` (Phase 4+, Decisions 1–8) gives employee
documents — signed contracts, ID copies, certifications — a home in WorkPulse.
Product decision 4 in `.ai/product.md` deferred the feature until a security
review; Decisions 7–8 of the feature spec close that review and adopt this
storage design (`## Decisions (PO, 2026-10-27)`).

The constraints the design must satisfy:

- Document bytes are PII. `.ai/security.md` pins the bar: presigned, short-lived
  URLs, never public paths, no sensitive data in logs, errors, events or audit
  values.
- The domain must not import an object-storage client (AGENTS.md architecture
  rule 3): storage access goes through a port and an infrastructure adapter.
- Uploads are capped at 20 MB and three MIME types; the API process should not
  proxy file bytes.
- An abandoned upload creates no row, no audit entry and no event.
- A document row is metadata only; replacing a file is delete + upload, not a
  version chain.

Alternatives considered:

- **Store bytes as `bytea` in PostgreSQL.** Rejected: 20 MB rows bloat the
  transactional database, backups and replication, and PostgreSQL is the
  source of truth for facts, not a blob store.
- **A public bucket or object URL.** Rejected outright: `.ai/security.md` says
  documents are presigned and never have a public path.
- **Stream uploads/downloads through the API.** Rejected: 20 MB bodies would
  flow through the application process and its memory/backpressure, and a
  scoped short-lived credential is a smaller authority than an authenticated
  API call that can reach every document.
- **A staging-intent table plus a server-side row before upload.** Rejected:
  the spec pins "the row exists only after confirm", so an abandoned upload
  must leave no row; an intent table would need its own reaper and contradict
  the aggregate's lifetime.
- **A presigned PUT instead of a presigned POST.** A PUT URL pins the method
  and key but not form-level conditions. A POST policy pins the exact key, the
  content type, the claimed size, the filename metadata channel and a
  content-length range in one signed document, so the browser cannot write a
  different object or a larger body than the policy allows.
- **`aioboto3`.** Rejected for v1: an extra dependency and wrapper for calls
  that are offloaded to a worker thread anyway; the port lets it swap in later
  without an API change.

## Decision

1. **A private MinIO bucket, presigned-only access.** MinIO is the S3-compatible
   object store in `docker-compose.yml` (dev `documents`, test
   `documents-test`), provisioned without anonymous access. The web origin is
   allowed by CORS so a browser can POST/GET a presigned URL directly. The API
   returns presigned URLs only — never a storage key, never a public path.
   Keys never contain filenames: the staging key is
   `staging/{tenant_id}/{upload_id}` and the final key is
   `documents/{tenant_id}/{employee_id}/{document_id}`.
2. **Upload is a stateless two-step protocol.**
   - **Intent** (`createDocumentUpload`): the service checks the manage policy,
     employee existence, the MIME allowlist (`application/pdf`, `image/png`,
     `image/jpeg`) and the `1..20971520` byte claim, then presigns a POST
     pinned to the exact staging key with `eq $key`,
     `eq $Content-Type <claimed>`, `eq $x-amz-meta-claimed-size <size_bytes>`,
     `starts-with $x-amz-meta-filename ""` and
     `content-length-range 1 <size_bytes>`. Upload URLs expire after 600 s by
     default (`storage_upload_ttl_seconds`, never above 10 min).
   - **Confirm** (`createDocument`): the server HEAD-checks the staged object,
     re-checks the type/size/claim (belt-and-braces), copies it server-side to
     the final key, purges the staging object, and inserts the row + audit
     entry + `document.uploaded` event in one database transaction.
   - **Download** (`downloadDocument`): a presigned GET is minted fresh on every
     call; it expires after 300 s by default (`storage_download_ttl_seconds`,
     never above 5 min). URLs are never cached server-side, logged, emitted or
     prefetched.
3. **The filename and the claim travel in staging-object metadata.**
   `DocumentCreate` has no filename or size field, so the client sends
   `x-amz-meta-filename` (percent-encoded UTF-8, at most 255 decoded
   characters) as a POST form field, alongside the signed
   `x-amz-meta-claimed-size`. Confirm reads both from the HEAD; missing or
   undecodable metadata is `409 document_upload_not_completed` (the pinned
   protocol was not completed). There is no staging-intent table: an abandoned
   upload leaves only an orphan staging object, cleaned by a bucket lifecycle
   rule (ops-level, out of scope for v1).
4. **Confirm copies before the row; delete purges before the row.**
   Confirm order is stat → checks → server-side copy → staging purge → row +
   audit + event in one transaction. A transaction failure after the copy
   leaves an orphan final object with no row and no event (bucket lifecycle /
   ops cleans it); never a row without bytes. Delete purges the object first,
   then sets `deleted_at` and writes the audit entry + `document.deleted` event
   in one transaction. A purge failure leaves the row, object and audit trail
   intact and the call retryable; a missing object is tolerated, because PII
   minimization already succeeded. A second delete is `404 document_not_found`.
5. **`DocumentStorage` is the domain port; MinIO is the adapter.**
   `apps/api/app/domains/documents/storage.py` defines `create_upload`, `stat`,
   `copy`, `delete` and `presign_download` over plain values;
   `apps/api/app/infrastructure/storage/minio.py` implements it with an
   S3-compatible client. The domain imports neither boto3 nor MinIO. Real S3
   later changes configuration and the adapter, not the port or the API
   surface.
6. **boto3 is the S3 client.** It has mature presigned-POST policy conditions
   and works against MinIO and S3 alike. Its blocking calls are offloaded with
   `anyio.to_thread.run_sync`; storage is not a hot path, and `aioboto3` can
   replace it behind the same port if that changes.
7. **Field-level encryption of document metadata is deferred (PO Decision 7).**
   Nothing in the codebase is field-encrypted today (salary included). The
   private bucket, presigned-only access, the metadata-only row and the
   no-logging rules cover the main exposure paths. A compliance requirement
   reopens this with its own ADR; the alternative (app-side AES-GCM with key
   management) is rejected for now.
8. **This ADR is part of the security-review record (PO Decision 8).** The
   feature spec, the `.ai/security.md` Documents/PII and capability rows, and
   this ADR are the review product decision 4 waited for. Approving the spec
   signs it off; no separate review artifact is required.

## Consequences

- Document bytes never touch the API process, a public URL or an event payload;
  the only access path is a short-lived URL scoped to one object.
- Two failure windows are accepted and documented rather than hidden: an
  orphan final object after a failed confirm transaction, and a duplicate
  confirm racing between copy and staging purge (the loser sees no staged
  object and gets `409`). Both fail safe; a bucket lifecycle rule is the
  cleanup mechanism.
- Presigned URLs are bearer credentials: they are returned only to an
  authorized caller, never logged, never cached and fetched per click. A stale
  URL is one click away from a fresh one.
- The two-step protocol is testable without storage: the presign policy is
  asserted against a stubbed client in unit tests, the API flow against a real
  MinIO test bucket in integration tests, and the full browser hop in e2e.
- Replacing MinIO with S3, or `boto3` with `aioboto3`, changes no API contract,
  no event contract and no web code.
