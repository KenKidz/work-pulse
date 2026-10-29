# Feature: Employee documents

- **Status:** done
- **Phase:** 4+ (the Employees context of `.ai/domain.md`; documents deferred here
  by product decision 4, `.ai/product.md` — "presigned URLs after a security
  review")
- **Size:** M
- **Surfaces:** api+web+events
- **Links:** `.ai/domain.md` (Employees context: documents; invariants 1–6;
  event catalog) · `.ai/product.md` (decision 4: documents → Phase 4+) ·
  `.ai/security.md` (the Documents row: presigned, short-lived URLs, never
  public paths; PII rules; audit log) · ADR 0004 (multi-tenant) · ADR 0007
  (soft-delete spirit for kept rows) · ADR 0010 (identity, `actor_id`) · ADR
  0012 (outbox, message key) · `employees.md` (the host aggregate + detail
  page; its decision 3 deferred this feature) · `recruitment.md` (candidate
  documents stay out — its PO decision 3) · `payroll.md` (payslip PDFs stay
  out) · `leave.md` / `overtime.md` / `performance.md` (manager scope = self +
  direct reports, the shared `EmployeeScope` port) · `i18n.md` (catalog
  conventions)

## Problem

Employee documents — signed contracts, ID copies, certifications — live in
email threads and shared folders. Nothing answers "who has a signed contract
on file?" or "which certifications expire this quarter?", and there is no
trail of who uploaded, changed or removed a file. WorkPulse promises every
sensitive workforce fact is auditable and PII-protected; documents are the
last piece of the employee record with no home. This feature gives them one:
HR maintains per-employee documents in private object storage, access happens
only through short-lived presigned URLs (the security-review outcome product
decision 4 waited for, codified in `.ai/security.md`), and every upload,
metadata change and deletion is audited.

## Business rules

- **Aggregate & ownership** — a Document belongs to exactly one employee in
  exactly one tenant. The row holds metadata only; the bytes live in private
  object storage. Fields: `title` (1..200), `category` (enum
  `contract|identification|certification|other`), `expiry_date` (optional
  `YYYY-MM-DD`, allowed for any category), `filename` (original name as
  supplied, ≤ 255), `content_type`, `size_bytes`, `uploaded_by` (actor user
  id, ADR 0010), `created_at`, `updated_at`, `deleted_at`.
- **Storage & presigned access** (product decision 4's security-review
  outcome): the bucket is **private** — no public path ever exists. Staging
  key on upload: `staging/{tenant_id}/{upload_id}`; final key:
  `documents/{tenant_id}/{employee_id}/{document_id}` (never a filename in a
  key). The API returns presigned URLs only: an upload intent is a presigned
  POST pinned to the exact staging key with a content-type allowlist and a
  `1..20 MB` content-length policy (TTL ≤ 10 min); a download is a presigned
  GET (TTL ≤ 5 min). TTLs are env-configurable with those defaults. Storage
  access goes through a `DocumentStorage` port — the domain never imports a
  MinIO/S3 client (architecture rule 3).
- **Upload is two-step**: (1) HR requests an upload intent with the claimed
  `content_type` and `size_bytes` → policy check → `201 DocumentUpload`; (2)
  the client uploads the file straight to storage; (3) the client confirms
  with the document metadata and `upload_id` → the server HEAD-checks the
  staged object (exists, size within limit), moves it to the final key
  (server-side copy), creates the row, writes the `document.uploaded` audit
  entry and emits `document.uploaded`. The row exists only after confirm: an
  abandoned upload creates no row, no audit entry, no event.
- **Allowed files**: `application/pdf`, `image/png`, `image/jpeg`; max 20 MB.
  Violations are refused at intent time and re-checked at confirm time
  (`document_type_not_allowed`, `document_too_large`).
- **Who may write**: upload, metadata update and delete are HR / Admin /
  Super Admin only (the *Manage employee records* capability). Never a
  manager, never self-service, never Finance. Denied → `403 forbidden`.
- **Who may read** (list, get, download): the owner (any role, their own
  documents — the "My documents" surface); a manager over self + direct
  reports (the house `EmployeeScope` port, one level — the exact scope that
  gates the leave/overtime/performance inboxes); HR / Admin / Super Admin
  everyone; Finance their own only. A same-tenant but out-of-scope read →
  `403 forbidden`; a foreign or unknown `employee_id` / `document_id` → `404`.
- **Metadata updates** (`title`, `category`, `expiry_date`) are field updates,
  not state transitions: audited as `document.updated`, **no event** (the
  `progress_pct` precedent in `performance.md`). At least one field per PATCH.
- **Delete is soft-delete + purge**: `deleted_at` is set on the row (kept for
  traceability — ADR 0007's spirit) and the object is deleted from storage
  (PII minimization). Deleted documents vanish from every list and return
  `404 document_not_found` on get/download; a second delete is a `404`. Audit
  `document.deleted` + event `document.deleted`.
- **PII**: `title`, `filename` and storage keys never appear in logs, error
  messages or event payloads. Event payloads carry `document_id`,
  `employee_id`, `category` only. Presigned URLs go only to the authorized
  caller and are never logged.
- **Tenant isolation**: rows and storage keys are tenant-scoped; every
  repository query filters `tenant_id`; a tenant-isolation test proves
  cross-tenant reads return 404 and storage is untouched.
- **No expiry behavior in v1**: `expiry_date` is stored and displayed (with an
  expired indicator at render); no reminders, no blocking, no workflows.
- A terminated employee keeps their documents; upload and delete remain
  allowed (offboarding paperwork).

## Scope

- The `document` table (migration `0019`) and the Documents domain module
  (models, schemas, service, repository, events, policies, exceptions).
- A `DocumentStorage` port plus the MinIO adapter in `apps/api/app/
  infrastructure/`; MinIO joins `docker-compose.yml` (private bucket
  provisioned for dev), `.env.example` gains endpoint/credentials/bucket/TTL
  entries. The storage decision gets an ADR (`0022`).
- The pinned API contract below and outbox producers for the two events
  (identifiers-only; every payload carries `employee_id`, so ADR 0012's
  message-key fallback needs no amendment).
- Web: a Documents tab on the employee detail page and a "My documents"
  section on the own-profile surface — upload dialog (file + title + category
  + expiry), metadata edit, delete dialog, download via a fresh presigned URL
  per click, en + vi catalog `documents.json`.
- Audit entries for upload, metadata update and delete.
- Docs updated in this change: `.ai/domain.md` gains the two `document.*`
  event-catalog rows, and `.ai/security.md` gains the two capability rows of
  *Roles & access*.

## Out of scope

- **Candidate documents / CVs** — still deferred (`recruitment.md` PO decision
  3). The `DocumentStorage` port is reusable there later; this feature ships
  employee documents only.
- **Payslip PDFs** — payslips stay simulated rows (`payroll.md`).
- **Versioning / replacement history** — replacing a file = delete + upload a
  new document.
- **Expiry reminders, notifications and compliance workflows** — `expiry_date`
  is display-only in v1.
- **Malware/antivirus scanning** of uploads (needs its own requirement + ADR).
- **Field-level encryption of document metadata** — see `## Decisions needed`.
- **Orphaned staging-object sweeping** — an abandoned upload leaves a staging
  object for a bucket lifecycle rule (ops-level); v1 documents the failure
  mode and does not build a sweeper.
- **Employee self-service upload** (PO decision: HR/Admin only).
- **Bulk / zip download and exports.**

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/documents/` |
| API router | `apps/api/app/api/v1/documents.py` (routes under `/api/v1/employees/{employee_id}/documents`) |
| Storage port | `DocumentStorage` protocol in the domain · MinIO adapter in `apps/api/app/infrastructure/` |
| Events | `contracts/events/documents/document-uploaded.v1.json` · `document-deleted.v1.json` |
| Workers | — (none; the API is the only writer) |
| Web feature | `apps/web/features/documents/` |
| Web routes | `apps/web/app/(dashboard)/employees/` (Documents tab on the employee detail page; "My documents" on the own-profile surface) |
| i18n | `apps/web/messages/en/documents.json` · `apps/web/messages/vi/documents.json` |
| Infra | `docker-compose.yml` (MinIO + private bucket) · `.env.example` |
| Migration | `apps/api/alembic/versions/0019_documents.py` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/documents.spec.ts` |

## Roles & access

These rows join `.ai/security.md`'s capability matrix with this feature:

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Upload / manage employee documents | — | — | Y | — | Y | Y |
| View / download employee documents | own | team (direct reports) | Y | own | Y | Y |

Enforcement lives in `domains/documents/policies.py` (never in routers, never
only in the UI). Cross-tenant reads return 404; out-of-scope same-tenant reads
return `403 forbidden`.

## API contract (pinned before coding)

All operationIds are the orval hook names and are pinned: the router sets them
as `operation_id` and the generated `@workpulse/api-client` hooks must match
exactly. Metadata field values are lowercase. Pagination is the house
`PageParams` clamp shape (clamp, never an error). Upload/download responses
carry presigned URLs only — never a storage key, never a public path.

| Method & path | operationId | Input | Response |
| --- | --- | --- | --- |
| `POST /api/v1/employees/{employee_id}/documents/uploads` | `createDocumentUpload` | `DocumentUploadCreate` | `201 DocumentUpload` |
| `POST /api/v1/employees/{employee_id}/documents` | `createDocument` | `DocumentCreate` | `201 Document` |
| `GET /api/v1/employees/{employee_id}/documents` | `listDocuments` | `?category=&page=&page_size=` | `200 {"items": Document[], "page": Page}` |
| `GET /api/v1/employees/{employee_id}/documents/{document_id}` | `getDocument` | — | `200 Document` |
| `GET /api/v1/employees/{employee_id}/documents/{document_id}/download` | `downloadDocument` | — | `200 DocumentDownload` |
| `PATCH /api/v1/employees/{employee_id}/documents/{document_id}` | `updateDocument` | `DocumentUpdate` | `200 Document` |
| `DELETE /api/v1/employees/{employee_id}/documents/{document_id}` | `deleteDocument` | — | `204` |

`DocumentUploadCreate`: `content_type` (allowlisted), `size_bytes` (int
1..20971520). `DocumentUpload`: `upload_id` (uuid), `url` (presigned POST
target), `fields` (form fields the client must send), `expires_at`.
`DocumentCreate`: `upload_id` (required), `title` (1..200, required),
`category` (required enum), `expiry_date` (`YYYY-MM-DD`, optional).
`Document`: `id`, `employee_id`, `title`, `category`, `expiry_date`,
`filename`, `content_type`, `size_bytes`, `uploaded_by`, `created_at`,
`updated_at` (deleted rows never serialize). `DocumentUpdate`: `title?`,
`category?`, `expiry_date?` (at least one). `DocumentDownload`: `url`,
`expires_at`.

Query semantics (pinned):

- `listDocuments` filters by `category`, excludes deleted rows, and is
  visibility-scoped first (roles table above), then filtered.
- `downloadDocument` returns a **fresh** URL on every call; URLs are never
  cached server-side and the client must never prefetch them.
- Foreign / unknown `employee_id` → `404 employee_not_found`; foreign /
  unknown / deleted `document_id` → `404 document_not_found`; out-of-scope
  same-tenant read → `403 forbidden`.

Error codes (stable): `document_type_not_allowed` (422) · `document_too_large`
(422) · `document_upload_not_completed` (409) · `document_not_found` (404) ·
`employee_not_found` (404) · `forbidden` (403).

## Events

Contracts land in `contracts/events/documents/` **before** the outbox
producers. Envelope per `contracts/events/common/event-envelope.v1.json`;
published on the outbox relay like `recruitment.md` / `performance.md`.
Metadata updates emit nothing (field update, not a state transition).

| `event_type` | Emitted when | Payload |
| --- | --- | --- |
| `document.uploaded` | a document row is created after confirm | `{document_id, employee_id, category}` |
| `document.deleted` | a document is soft-deleted and its object purged | `{document_id, employee_id, category}` |

No `title`, `filename`, storage key or presigned URL ever reaches a payload
(`.ai/security.md` PII rules). Every payload carries `employee_id`, so ADR
0012's message-key fallback stays valid with no amendment. Catalog rows land
in `.ai/domain.md` with the feature (Contract 4+, Published 4+).

## Edge cases

- Upload intent requested but the file never uploaded → confirm returns `409
  document_upload_not_completed`; no row, no audit entry, no event. The orphan
  staging object is left to a bucket lifecycle rule (out of scope).
- Confirm replayed after success (same `upload_id`) → `409
  document_upload_not_completed` (the staged object is gone after the move).
- Confirm after the presigned URL expired but the object landed in time →
  succeeds: the staged object, not the URL lifetime, decides.
- Staged object larger than the claim (or over the cap) → `422
  document_too_large` at confirm; storage policy should have refused the PUT
  first (belt-and-braces).
- Two documents with the same `title` → allowed; titles are not unique.
- A past `expiry_date` at upload → allowed (historical records); the UI flags
  it as expired.
- Delete twice → the second call is `404 document_not_found`.
- A stale download URL opening in the browser → the client re-requests a URL
  on every click, so a fresh one is one click away; the failure is browser-side
  and never leaks to other documents.
- Terminated employee → documents stay readable; upload/delete stay allowed.
- Employee with zero documents → empty list state on both surfaces.
- A `filename` containing PII → stored, never logged, never echoed in errors.
- Cross-tenant `employee_id` or `document_id` → `404`, and storage is never
  touched.

## Acceptance criteria

- [x] API (upload intent): given HR acting on an employee, when
      `createDocumentUpload` posts `application/pdf` at 1 MB, then 201 returns
      a presigned POST pinned to `staging/{tenant_id}/{upload_id}` expiring
      within 10 minutes. — `test_documents_api.py::test_upload_intent_returns_presigned_post_for_the_staging_key`
      (AC1), `test_service.py::test_create_upload_presigns_the_staging_key`,
      `test_storage.py::test_create_upload_pins_the_staging_policy`,
      `test_storage.py::test_create_upload_uses_the_configured_default_ttl`,
      `test_storage.py::test_create_upload_clamps_an_overlong_ttl`.
- [x] API (intent failure): given `content_type: "text/html"` or `size_bytes`
      over 20 MB, when `createDocumentUpload` posts, then 422
      `document_type_not_allowed` / `document_too_large` and no presigned URL
      is returned. — `test_documents_failures.py::test_intent_rejects_disallowed_type_or_size`
      (AC2), `test_service.py::test_create_upload_rejects_a_type_outside_the_allowlist`,
      `test_service.py::test_create_upload_rejects_a_size_over_the_cap`.
- [x] API (confirm): given a staged object in storage, when `createDocument`
      confirms with title/category/expiry, then 201 returns a Document whose
      `filename`/`content_type`/`size_bytes` match the staged object, the
      object now lives at `documents/{tenant_id}/{employee_id}/{document_id}`,
      a `document.uploaded` audit entry exists, and the outbox row carries ids
      + category only. — `test_documents_api.py::test_confirm_moves_the_object_creates_the_row_and_emits_ids_only`
      (AC3), `test_service.py::test_confirm_copies_purges_and_writes_row_audit_and_event`,
      `test_documents_events.py`.
- [x] API (confirm failure): given no staged object for the `upload_id`, when
      `createDocument` confirms, then 409 `document_upload_not_completed` with
      no row, no audit entry and no event. — `test_documents_api.py::test_confirm_without_a_staged_object_writes_nothing`
      (AC4), `test_documents_failures.py::test_confirm_without_an_object_is_incomplete`
      / `test_confirm_replay_after_success_is_incomplete`,
      `test_service.py::test_confirm_without_a_staged_object_writes_nothing`.
- [x] API (denied write): given an Employee, Manager or Finance identity, when
      any upload/confirm/update/delete call is made, then 403 `forbidden` with
      no row and no audit entry. — `test_documents_roles.py::test_non_management_roles_cannot_write`
      (AC5), `test_policies.py::test_non_management_roles_may_not_manage`,
      `test_service.py::test_create_upload_denied_for_non_management`.
- [x] API (read scope): given a manager, when `listDocuments` runs for self and
      for a direct report, then both lists return; when it runs for a
      non-report, then 403 `forbidden`. — `test_documents_roles.py::test_read_scope_matrix`
      (AC6), `test_service.py::test_manager_lists_self_and_direct_reports_only`.
- [x] API (denied read): given an Employee identity naming another employee's
      document, when `downloadDocument` runs, then 403 `forbidden` and no
      presigned URL is returned. — `test_documents_roles.py::test_denied_download_returns_no_presigned_url`
      (AC7), `test_service.py::test_denied_download_mints_no_url`.
- [x] API (delete): given HR on an existing document, when `deleteDocument`
      runs, then 204, the object is gone from storage, `deleted_at` is set,
      list/get/download all return 404, and a `document.deleted` audit entry +
      outbox row exist; a second delete returns 404. — `test_documents_api.py::test_delete_purges_the_object_and_hides_every_read`
      (AC8), `test_documents_failures.py::test_deleted_document_is_404_on_every_operational_read`,
      `test_service.py::test_delete_purges_the_object_before_soft_deleting`.
- [x] API (metadata update): when `updateDocument` changes `title`, then 200
      returns the new value with a `document.updated` audit entry and **no**
      outbox row. — `test_documents_api.py::test_update_metadata_is_audited_without_an_event`
      (AC9), `test_service.py::test_update_audits_title_without_its_value_and_emits_no_event`,
      `test_documents_audit.py::test_documents_are_audited_without_pii`.
- [x] API (tenant isolation): given another tenant's identity naming the same
      document id, when any read/download/update/delete runs, then 404
      `document_not_found` and storage is untouched. — `test_documents_tenant_isolation.py::test_cross_tenant_document_access_is_404_and_leaves_storage_alone`
      (AC10), `test_service.py::test_a_foreign_tenant_cannot_reach_a_document`.
- [x] Web (HR): given the employee detail page, when HR completes the upload
      dialog (file + title + category + expiry), then the row appears in the
      Documents tab with category and expiry and a success toast; download
      opens the presigned URL in a new tab. — `apps/web/tests/documents/upload-document-dialog.test.tsx::uploads to storage, then confirms the metadata and closes`,
      `documents-panel.test.tsx::lists documents with category and expiry, and gives HR every action`
      / `mints a fresh download URL per click and opens it in a new tab`.
- [x] Web (My documents): given an Employee identity, when the own-profile
      "My documents" surface loads, then only their own documents are listed
      and no upload/delete control is rendered. — `apps/web/tests/documents/my-documents.test.tsx::lists the current employee's documents and never renders write controls`.
- [x] Web (manager read-only): given a Manager on a direct report's detail
      page, then the Documents tab lists the report's documents with no
      upload/delete controls. — `documents-panel.test.tsx::gives a manager a read-only list on a direct report's record`
      / `shows the manager's read-only table inside the employee detail workspace copy`.
- [x] Web (failure): when the confirm call fails, then a toast shows the
      pinned `document_upload_not_completed` copy and the list is unchanged;
      when the list fetch fails, the tab shows the error state with retry. —
      `upload-document-dialog.test.tsx::shows the pinned toast and keeps the dialog open when confirm fails`,
      `documents-panel.test.tsx::shows the error state with a retry that refetches the list`.
- [x] Web (expired): given a document with a past `expiry_date`, when it is
      rendered, then it shows the expired indicator in en and vi. —
      `documents-panel.test.tsx::lists documents with category and expiry, and gives HR every action`
      (Expired badge) / `has the documents surface translated in Vietnamese`
      (`documents.expired` = "Đã hết hạn").
- [x] E2E: given a seeded tenant, when HR uploads a document to an employee
      through the UI, then the employee sees it under "My documents" and can
      download it; after HR deletes it, it disappears from both surfaces and a
      direct download request returns 404. — `tests/e2e/documents.spec.ts::HR uploads, the employee downloads, HR deletes`
      (AC16).

## Decisions (PO, 2026-10-27)

1. **Object storage: MinIO + presigned URLs.** S3-compatible MinIO joins
   docker compose; upload and download happen only through short-lived
   presigned URLs on a private bucket. Rationale: matches the architecture
   target diagram's Object Storage slot and `.ai/security.md`'s Documents row
   literally ("presigned, short-lived URLs; never public paths"), and a real
   S3 deployment later changes no API surface.
2. **HR/Admin-only writes.** Upload, metadata update and delete are HR / Admin
   / Super Admin. Rationale: product.md gives HR the employee-record
   maintenance job; one writer class keeps the audit story simple.
3. **Reads: owner + team manager + HR/Admin.** Owner (any role, own
   documents), manager over self + direct reports (the house `EmployeeScope`
   port), HR / Admin / Super Admin everyone; Finance own only. Rationale:
   consistent with team-profile visibility, one level deep because documents
   are the most sensitive employee data.
4. **Metadata: category enum + title + expiry.** `category` ∈
   `contract|identification|certification|other`, `title` 1..200, optional
   `expiry_date`. Rationale: an enum stays filterable; expiry makes
   certifications actionable later without any v1 behavior attached.
5. **Delete = soft-delete row + purge file.** The row is kept with
   `deleted_at` for traceability (ADR 0007's spirit); the object is purged
   (PII minimization). Rationale: the audit trail must show a document
   existed; the bytes should not outlive the record's usefulness.
6. **UI: Documents tab on employee detail + "My documents" on the own
   profile.** Rationale: documents live where the employee record already
   lives; employees get self-visibility without write access.
7. **Field-level encryption of document metadata: deferred** (approval
   2026-10-27; closes the former *Decisions needed* item). Nothing in the
   codebase is field-encrypted today (salary included), and the private bucket
   + presigned URLs + no-logging rules already cover the main exposure paths;
   the storage ADR (0022) records the deferral so a compliance requirement can
   reopen it. Alternative rejected: encrypt `title`/`filename` app-side now
   (AES-GCM + key management + its own ADR).
8. **Security-review sign-off** (approval 2026-10-27; closes the former
   *Decisions needed* item). This spec, the `.ai/security.md` Documents/PII
   rows and the storage ADR (0022) *are* the security-review record that
   product decision 4 waited for; approving this spec signs it off.
   Alternative rejected: a separate written review artifact before
   implementation.

## Plan

Planned & approved 2026-10-27 (the approval adopts Decisions 7–8 and closes
the former *Decisions needed* item). Contract-first: T1 pins the wire shapes
(schemas + stable error codes) and the two event contracts **before** any
producer, and `make gen-api` (T5) freezes `contracts/openapi/openapi.json` +
`@workpulse/api-client` **before** any web task, so the surfaces cannot drift.
Order is contracts → storage → domain/API → web → e2e → record, with `make
check` green at the end of every task (the narrowest covering gate mid-slice:
`make check-api` for T1–T5, `make check-web` for T6–T7, full `make check`
from T8 on).

### 1. Acceptance criteria → changes

| AC | Satisfied by |
| --- | --- |
| Upload intent 201: presigned POST pinned to `staging/{tenant_id}/{upload_id}`, ≤ 10 min | T2 `DocumentStorage.create_upload` (policy pins exact key + claimed content-type/size; TTL default 600 s env-configurable); T4 `DocumentService.create_upload`; T5 `createDocumentUpload` route + `test_documents_api.py` |
| Intent failures 422 `document_type_not_allowed` / `document_too_large`, no URL returned | T1 exceptions; T4 service validates before presigning; `test_documents_failures.py` |
| Confirm 201: metadata matches staged object, object at `documents/{tenant_id}/{employee_id}/{document_id}`, `document.uploaded` audit, outbox carries ids + category only | T3 `Document` model + migration `0019`; T4 `create` (stat → re-checks → server-side copy → staging purge → row + audit + event in one tx) + `events.py` builders; T5 `test_documents_api.py`, `test_documents_audit.py`, `test_documents_events.py` |
| Confirm failure 409 `document_upload_not_completed`: no row, no audit, no event | T4 confirm performs stat/checks before any write; `test_documents_failures.py` |
| Denied write 403 for Employee / Manager / Finance on any upload/confirm/update/delete | T3 `policies.ensure_can_manage`; T5 `test_documents_roles.py` |
| Read scope: manager lists self + direct report; non-report → 403 | T3 `policies.ensure_can_read` over the house `EmployeeScope` port (one level); `test_documents_roles.py` |
| Denied read 403: Employee downloads another employee's document, no URL | T4 `download` runs the scope check before presigning; `test_documents_roles.py` |
| Delete: 204, object purged, `deleted_at` set, list/get/download 404, `document.deleted` audit + outbox; second delete 404 | T4 `delete` (purge object first, then row + audit + event); `test_documents_api.py`, `test_documents_failures.py` |
| Metadata update: 200 + `document.updated` audit and **no** outbox row | T4 `update` (field update, the `progress_pct` precedent); unit `test_service.py` asserts the outbox is untouched; `test_documents_audit.py` |
| Tenant isolation: 404 `document_not_found`, storage untouched | T3 repository tenant-first queries; T5 `test_documents_tenant_isolation.py` |
| Web (HR): upload dialog → row in Documents tab with category + expiry, success toast; download opens fresh presigned URL in a new tab | T6 `UploadDocumentDialog` + `DocumentsPanel` + `useCreateDocumentUpload`/`useCreateDocument`; download resolves `useDownloadDocument` per click and opens `url` |
| Web (My documents): own documents only, no upload/delete controls | T7 `/profile` surface with `canWrite=false` |
| Web (manager read-only): report's Documents tab lists without upload/delete controls | T6 role gating (`roles.ts`) renders write controls for HR/Admin/Super Admin only |
| Web (failure): confirm failure toast with pinned `document_upload_not_completed` copy, list unchanged; list fetch failure → error state + retry | T6 `labels.ts` code→copy map + `DocumentsPanel` states; `apps/web/tests/documents/` |
| Web (expired): past `expiry_date` shows expired indicator in en and vi | T6 `messages/{en,vi}/documents.json` + `DocumentRow` badge |
| E2E: HR uploads → employee sees it under "My documents" and downloads → HR deletes → gone from both surfaces, direct download 404 | T8 `tests/e2e/documents.spec.ts` |

### 2. Repo findings the plan relies on (verified 2026-10-27)

- **Migration `0019` and ADR `0022` are free** (migration head = `0018`;
  ADRs run `0001`–`0021`).
- **Errors** are `WorkPulseError` subclasses rendered as
  `{"error": {"code", "message"}}` by `shared/errors.register_error_handlers`.
  The generic `forbidden` (403) already exists (`shared.errors.ForbiddenError`)
  and matches this spec (recruitment's `recruitment_denied` is the outlier).
  All six pinned codes map to subclasses without touching the handler.
- **Pagination** is exactly the pinned shape: `PageParams` clamps
  (`page ≥ 1`, `1 ≤ page_size ≤ 100`), never errors; list envelope is
  `{"items": [...], "page": Page}` (`app/shared/pagination.py`).
- **Outbox**: `EventEnvelope` requires `data.employee_id` (first in
  `_MESSAGE_KEY_FIELDS`); both document events carry `employee_id`, so **no
  ADR 0012 amendment** is needed (unlike recruitment). Builders follow
  `performance/events.py` (`_envelope` + pure per-event fns); the emit pattern
  is repo write → `outbox.append(...)` → `audit.write(...)` on the request
  session, committed once per request.
- **Audit** is `AuditWriter.write(AuditEntryCreate(...))` (insert-only,
  dot-lowercase actions like `document.uploaded`); PII fields are recorded
  with the `PII_CHANGED` sentinel, values never (recruitment precedent) —
  `title`/`filename` values must never appear in `changes`.
- **`EmployeeScope` is not a shared module**: there are two per-domain
  Protocol copies (`performance/repository.py`, `reports/ports.py`) over one
  adapter `PostgresEmployeeScope` (`app/infrastructure/postgres/
  manager_scope.py` = self OR `manager_id = manager`, one level). Documents
  declares its own Protocol copy and injects the shared adapter (house
  pattern) — there is no central protocol to import.
- **No storage code or dependency exists** (`pyproject.toml` has no
  boto3/aioboto/minio); `app/infrastructure/README.md` pre-reserves a
  `storage/` package "when a spec needs them". Config is `pydantic-settings`
  flat snake_case with dev/test twin pairs (`clickhouse_test_url` pattern);
  docker-compose uses twin services (`postgres-test`, `redis-test`,
  `kafka-test`, `clickhouse-test`) with loopback ports + healthchecks.
- **Soft delete**: the codebase only has `archived_at` on reference data
  (ADR 0007, idempotent no-op delete). `deleted_at` is a new column, and the
  spec's second-delete `404` deliberately differs from that no-op precedent.
  Nothing blocks writes to terminated employees globally (payroll is
  status-blind; recruitment guards only interviewer assignment), so
  "terminated keeps documents, upload/delete stay allowed" has no firewall to
  fight.
- **Web**: `employees/[id]/page.tsx` renders a single `EmployeeDetailCard` —
  **no tabs exist there yet** (Tabs appear only in
  `features/performance/components/PerformanceWorkspace.tsx`), and **no
  own-profile surface exists** (no route, no `NAV_ITEMS` entry). There is
  **no file-input/upload code anywhere** in `apps/web` — the two-step
  presigned upload is a genuinely new surface (JSON react-hook-form flows
  everywhere else; the storage POST itself is plain `fetch`/`FormData`).
  Feature modules follow `features/performance/` (`api.ts`, `hooks/`,
  `components/`, `labels.ts`, `roles.ts`, `schema.ts`); `ApiError.code` maps
  to i18n sentences via `labels.ts`; toasts are `sonner`.
- **i18n**: per-surface `messages/{en,vi}/<surface>.json` merged only in
  `messages/<locale>/index.ts`; en/vi drift fails the web tests.
- **Gates**: `make check` = `check-api` (ruff + ruff format + pyright +
  pytest, incl. integration/contract against the `:5433` test DB) +
  `check-web` (typecheck + lint + vitest) + `check-e2e`
  (`tests/e2e/full-hop.sh` + Playwright at repo root). `make gen-api` =
  `scripts/export_openapi.py` + orval → `packages/api-client`;
  `tests/contract/test_openapi_drift.py` pins the committed OpenAPI.
- **Integration harness**: `client_factory(identity)` with `TENANT_A` /
  `TENANT_B`, `get_identity`/`get_session` overrides, deterministic clock on
  the router's `_now`, outbox rows asserted directly, and a conftest
  truncation list that must gain the `document` table.
- Spec gaps this plan pins (§5): the filename/claim transport, the stateless
  upload intent, and the missing own-profile host surface.

### 3. Ordered tasks

#### T1 — Pin API + event contracts, catalog/security rows, ADR 0022

**Changes**

- Event contracts **first** (no producer/consumer exists yet):
  `contracts/events/documents/document-uploaded.v1.json` and
  `document-deleted.v1.json` — envelope per
  `contracts/events/common/event-envelope.v1.json`, `data` =
  `{document_id, employee_id, category}` exactly, `additionalProperties:
  false`, Draft 2020-12, PII rule in `description` (identifiers only).
- `apps/api/app/domains/documents/{__init__,schemas,exceptions}.py`: the wire
  shapes exactly per the API contract table (`DocumentUploadCreate`,
  `DocumentUpload`, `DocumentCreate`, `Document`, `DocumentUpdate` with an
  at-least-one-field validator, `DocumentDownload`), constraints (title
  1..200, `size_bytes` 1..20971520, category enum, `YYYY-MM-DD` expiry,
  filename ≤ 255) and the stable error codes as `WorkPulseError` subclasses:
  `document_type_not_allowed` (422), `document_too_large` (422),
  `document_upload_not_completed` (409), `document_not_found` (404),
  `employee_not_found` (404); `forbidden` reuses
  `shared.errors.ForbiddenError`. Field descriptions pin the upload-protocol
  metadata channel (§5.1–5.2).
- `.ai/domain.md`: the two `document.*` event-catalog rows (Contract 4+,
  Published 4+). `.ai/security.md`: the two capability rows from *Roles &
  access* above.
- **ADR 0022** (`.ai/decisions/0022-document-storage-minio-presigned.md`):
  private MinIO bucket, presigned-only access; stateless two-step upload with
  the claim pinned in the POST policy; object-metadata filename/claim
  channel; purge-first delete + copy-before-row confirm orderings (and their
  failure modes); boto3 as the S3-compatible client; field-encryption
  deferral (Decision 7) and the security-review record (Decision 8).
- Unit tests `tests/unit/documents/test_schemas.py`: bounds, enum,
  at-least-one, and the error-code ↔ status pairs.

**Files:** `contracts/events/documents/*.v1.json` ·
`apps/api/app/domains/documents/{__init__,schemas,exceptions}.py` ·
`.ai/domain.md` · `.ai/security.md` · `.ai/decisions/0022-*` ·
`apps/api/tests/unit/documents/test_schemas.py`.

**Verify:** `make check-api`. Both event files validate against the envelope
schema (builder contract tests land in T5). No producer exists yet, so the
"contract before producer/consumer" rule holds.

#### T2 — Storage: `DocumentStorage` port + MinIO adapter + infra

**Changes**

- `apps/api/app/domains/documents/storage.py`: `DocumentStorage` Protocol —
  `create_upload(tenant_id, upload_id, *, content_type, size_bytes, ttl)` →
  `PresignedUpload(url, fields, expires_at)`; `stat(key)` →
  `ObjectStat | None` (size, content_type, user metadata); `copy(src, dst)`;
  `delete(key)`; `presign_download(key, ttl)` → `(url, expires_at)`. The
  domain never imports an S3/MinIO client (architecture rule 3).
- `apps/api/app/infrastructure/storage/{__init__,minio}.py`:
  `MinioDocumentStorage` (boto3 client, S3-compatible). `create_upload`
  presigns a **POST** with policy conditions: `eq $key staging/
  {tenant_id}/{upload_id}`, `eq $Content-Type <claimed>`,
  `eq $x-amz-meta-claimed-size <size_bytes>`, `starts-with
  $x-amz-meta-filename ""`, `content-length-range 1 <size_bytes>` (claim is
  validated 1..20 MB at intent, so the PUT refuses over-claim uploads); the
  returned `fields` carry the signed fixed fields plus
  `x-amz-meta-claimed-size`. Blocking calls are offloaded via
  `anyio.to_thread.run_sync`.
- `docker-compose.yml`: `minio` (:9000 API, :9001 console, loopback),
  `minio-test` (:9002/:9003) and a `minio-init` one-shot (`minio/mc`)
  provisioning the private buckets `documents` / `documents-test`;
  `MINIO_API_CORS_ALLOW_ORIGIN=http://localhost:3000` so browsers can
  POST/PUT to presigned URLs. House style: pinned image tag, loopback ports,
  fixed dev creds, healthcheck.
- `Makefile`: MinIO joins the dev and test-infra bring-up (`make dev`,
  `make check-api`'s infra target, `make check-e2e` prerequisites).
- `apps/api/app/config.py` + `.env.example`: `storage_endpoint`,
  `storage_access_key`, `storage_secret_key`, `storage_bucket`,
  `storage_upload_ttl_seconds` (default 600), `storage_download_ttl_seconds`
  (default 300) plus `storage_test_*` twin overrides (the
  `clickhouse_test_url` pattern); tests pin `STORAGE_* → STORAGE_TEST_*` in
  `tests/conftest.py` like `DATABASE_URL`.
- `apps/api/pyproject.toml`: `uv add boto3` (justified in ADR 0022: mature
  presigned-POST policy conditions; S3-compatible so a real S3 deployment
  later changes no API surface or adapter).

**Files:** `apps/api/app/domains/documents/storage.py` ·
`apps/api/app/infrastructure/storage/{__init__,minio}.py` ·
`docker-compose.yml` · `Makefile` · `apps/api/app/config.py` · `.env.example` ·
`apps/api/pyproject.toml` · `apps/api/tests/unit/documents/test_storage.py` ·
`apps/api/tests/conftest.py` (env pinning).

**Verify:** `make check-api`. Unit tests assert the presign policy/fields/TTL
against a stubbed client (exact key, eq content-type, eq claimed-size,
filename channel, `content-length-range`, GET TTL ≤ 5 min).

#### T3 — Model, migration `0019`, policies, repository

**Changes**

- `apps/api/app/domains/documents/models.py`: `Document` per the aggregate —
  `id` (uuid PK), `tenant_id`, `employee_id`, `title` String(200), `category`
  String + `CheckConstraint` (house enum style), `expiry_date` Date | None,
  `filename` String(255), `content_type`, `size_bytes`, `uploaded_by`
  (actor user id, ADR 0010), `created_at`, `updated_at`, `deleted_at`.
  Tenant-first indexes, incl. a list partial index on
  `(tenant_id, employee_id, created_at DESC) WHERE deleted_at IS NULL`
  declared **both** in `__table_args__` (`postgresql_where`,
  `postgresql_include`) and in the migration (parity rule).
- `apps/api/alembic/versions/0019_documents.py` (`revision "0019"`,
  `down_revision "0018"`); `tests/integration/conftest.py` truncation list
  gains `document`.
- `apps/api/app/domains/documents/policies.py`: `ensure_can_manage(identity)`
  (HR/Admin/Super Admin, else 403 `forbidden` via `ForbiddenError`);
  `ensure_can_read(identity, target_employee_id, visible_ids)` — owner (any
  role) passes; Manager passes when the target is self or in
  `EmployeeScope.visible_employee_ids` (one level); HR/Admin/Super Admin pass
  everyone; Finance only self. Per-domain `EmployeeScope` Protocol copy over
  the shared `PostgresEmployeeScope` (house pattern).
- `apps/api/app/domains/documents/repository.py` (Protocol) +
  `apps/api/app/infrastructure/postgres/repositories/documents.py`:
  tenant-first queries, `deleted_at IS NULL` for all visible reads, `category`
  filter + `PageParams` list with total, `update` field patch, `soft_delete`
  returning `changed=False` when already deleted (service maps that to 404 —
  the spec's second-delete rule, deliberately unlike the `archived_at` no-op).
- Employee existence check for `404 employee_not_found`: reuse an existing
  in-tenant reader (`infrastructure/postgres/employee_usage.py` /
  `employee_link.py`) if one already exposes existence; otherwise add
  `EmployeeReferenceReader` (documents-local Protocol) +
  `PostgresEmployeeReferenceReader` — never query the Employees tables
  directly from the documents repository.

**Files:** `apps/api/app/domains/documents/{models,policies,repository}.py` ·
`apps/api/alembic/versions/0019_documents.py` ·
`apps/api/app/infrastructure/postgres/repositories/documents.py` ·
`apps/api/tests/unit/documents/test_policies.py` ·
`apps/api/tests/integration/conftest.py`.

**Verify:** `make check-api` (the migration runs in the integration
session; `test_migrations.py` stays green). `test_policies.py` covers the
full role matrix incl. every denied cell.

#### T4 — Service, event builders, audit + outbox wiring

**Changes**

- `apps/api/app/domains/documents/events.py`: `_envelope` +
  `document_uploaded(...)` / `document_deleted(...)` pure builders — payload
  `{document_id, employee_id, category}` exactly (never title/filename/key/
  URL); message key resolves to `employee_id` through the existing fallback.
- `apps/api/app/domains/documents/service.py` (`DocumentService` — the only
  business logic; `identity` first, `now` last, `uuid7()` ids):
  - `create_upload`: manage check → employee exists (else 404) →
    type allowlist + size 1..20 MB (else the 422s) → `storage.create_upload`.
  - `create` (confirm): manage check → employee exists → `storage.stat`
    (missing → 409 `document_upload_not_completed`) → re-check content-type
    ∈ allowlist (else 422 `document_type_not_allowed`), size ≤ cap and ≤
    claimed (else 422 `document_too_large`), `x-amz-meta-filename` present
    and decodable (else 409) → generate `document_id` → server-side copy to
    `documents/{tenant_id}/{employee_id}/{document_id}` → delete staging
    object → insert row (filename/content_type/size from the HEAD) + audit
    `document.uploaded` (`PII_CHANGED` for filename) + outbox
    `document.uploaded` in one transaction.
  - `list` / `get` / `download`: read scope first (403), then rows (404 for
    foreign/deleted); `download` presigns a **fresh** GET per call (TTL
    default 300 s), never caches.
  - `update`: manage check → at-least-one field → patch → audit
    `document.updated` (`title` as `PII_CHANGED`, category/expiry old→new) →
    **no** outbox append.
  - `delete`: manage check → row (404 if missing/already deleted) →
    `storage.delete` first (missing object tolerated; storage failure aborts
    untouched and is retryable) → set `deleted_at` + audit `document.deleted`
    + outbox `document.deleted` in one transaction.
- Unit tests `apps/api/tests/unit/documents/{fakes.py,test_service.py}`
  (`FakeDocumentStorage`, `FakeEmployeeScope`, fake repo/audit/outbox — the
  `tests/unit/<domain>/fakes.py` pattern).

**Files:** `apps/api/app/domains/documents/{events,service}.py` ·
`apps/api/tests/unit/documents/{fakes.py,test_service.py}`.

**Verify:** `make check-api`. Unit tests cover: intent 422s (AC2), confirm
409 with zero writes (AC4), confirm happy path payload = ids + category only
(AC3), update writes audit and **zero** outbox rows (AC9), delete purge-first
+ second delete 404 (AC8), and the PII sentinel in every audit `changes`.

#### T5 — Router, wiring, API/contract tests, api-client regen

**Changes**

- `apps/api/app/api/v1/documents.py` (thin: `_now()`, `IdentityDep`,
  `ServiceDep`, `PageParamsDep`) with the pinned `operation_id`s exactly
  `createDocumentUpload`, `createDocument`, `listDocuments`, `getDocument`,
  `downloadDocument`, `updateDocument`, `deleteDocument` under
  `/api/v1/employees/{employee_id}/documents`; register in
  `app/api/v1/__init__.py`; wire `get_document_service` + `DocumentServiceDep`
  in `app/dependencies.py` (MinioDocumentStorage + Postgres repo +
  PostgresEmployeeScope + PostgresAuditWriter + PostgresEventOutbox + the
  employee reader — the composition-root docstring order).
- `make gen-api` — commits `contracts/openapi/openapi.json` +
  `packages/api-client` (drift test enforces it); the generated hooks
  `useCreateDocumentUpload` … `useDeleteDocument` become the only web entry.
- Integration (real DB + MinIO-test): `tests/integration/test_documents_api.py`
  (AC1/3/8/9), `test_documents_failures.py` (422s at intent **and** confirm
  re-checks, 409 confirm/replay, delete-twice 404, deleted rows 404 on
  get/download), `test_documents_roles.py` (AC5/6/7 incl. manager self /
  report / non-report), `test_documents_tenant_isolation.py` (AC10 —
  cross-tenant read/update/delete → `404 document_not_found`, foreign
  `employee_id` → `404 employee_not_found`, storage untouched),
  `test_documents_audit.py` (three actions, sentinel, no PII values).
  Conftest gains a MinIO-test bucket reset helper (per-test prefix or purge).
- Contract: `tests/contract/test_documents_events.py` (builders vs the two v1
  schemas + envelope, PII-key sweep, `kafka_message_key` = `employee_id`) and
  the event→contracts-dir mapping entries in
  `tests/integration/test_outbox_contracts.py` (plus any catalog sweep that
  enumerates `contracts/events/`).

**Files:** `apps/api/app/api/v1/documents.py` ·
`apps/api/app/api/v1/__init__.py` · `apps/api/app/dependencies.py` ·
`contracts/openapi/openapi.json` + `packages/api-client` (generated) ·
`apps/api/tests/integration/test_documents_*.py` +
`test_outbox_contracts.py` · `apps/api/tests/contract/test_documents_events.py`.

**Verify:** `make check-api` **and** `make check-web` (the regenerated client
must typecheck). `make gen-api` diff-free afterwards.

#### T6 — Web: Documents module + employee-detail Documents tab

**Changes** (UI work: load the `workpulse-ui` skill first; `shadcn` if
`components/ui/tabs` needs installing)

- `apps/web/features/documents/`: `api.ts` (query keys + invalidation of
  list/detail), `hooks/use-documents.ts` (thin wrappers over
  `useCreateDocumentUpload`, `useCreateDocument`, `useListDocuments`,
  `useGetDocument`, `useDownloadDocument`, `useUpdateDocument`,
  `useDeleteDocument`), `components/` (`DocumentsPanel` — list, loading /
  empty / error+retry states; `UploadDocumentDialog` — file + title +
  category + expiry via react-hook-form + zod, then the two-step flow:
  `fetch(url, {method: "POST", body: FormData})` to the presigned POST with
  `x-amz-meta-filename` added to `fields`, then `createDocument` confirm;
  `EditDocumentDialog`; `DeleteDocumentDialog`; `DocumentRow` with expired
  badge), `labels.ts` (stable-code → i18n keys incl. the pinned
  `document_upload_not_completed` toast copy), `roles.ts`
  (`canManageDocuments` = HR/Admin/Super Admin), `schema.ts`, `types.ts`,
  `dates.ts`.
- Employee detail: introduce a two-tab shell (Overview | Documents) around
  `EmployeeDetailCard` in `apps/web/app/(dashboard)/employees/[id]/` +
  `features/employees/`; the Documents tab renders `DocumentsPanel` with
  write controls only for HR/Admin/Super Admin (Manager/Employee see a
  read-only list — AC13).
- i18n: `apps/web/messages/en/documents.json` +
  `messages/vi/documents.json` (incl. expired indicator + error copy) merged
  into both `messages/<locale>/index.ts`.
- Web tests `apps/web/tests/documents/*.test.tsx` (api-client mocked
  wholesale, the house pattern).

**Files:** `apps/web/features/documents/**` ·
`apps/web/app/(dashboard)/employees/[id]/` + `features/employees/` (tab
shell) · `apps/web/messages/{en,vi}/documents.json` +
`messages/{en,vi}/index.ts` · `apps/web/tests/documents/`.

**Verify:** `make check-web`.

#### T7 — Web: "My documents" own-profile surface

**Changes**

- New minimal own-profile route `apps/web/app/(dashboard)/profile/page.tsx`
  whose v1 content is the "My documents" section: `DocumentsPanel` for the
  current employee (`useGetCurrentEmployee`), **read-only for every role**
  (no upload/edit/delete controls — AC12). Nav entry in `NAV_ITEMS` +
  `messages/{en,vi}/nav.json` (all roles). This surface does not exist today
  (repo finding §2 / risk §5.3); a full profile page is out of scope.
- Web tests for the surface (own-only rendering, zero write controls).

**Files:** `apps/web/app/(dashboard)/profile/page.tsx` ·
`NAV_ITEMS` + `apps/web/messages/{en,vi}/nav.json` ·
`apps/web/tests/documents/` (profile cases).

**Verify:** `make check-web`.

#### T8 — E2E

**Changes**

- `tests/e2e/documents.spec.ts` (+ tiny `tests/e2e/fixtures/` pdf/png): one
  seeded tenant; HR context uploads through the dialog on the employee
  detail → row visible with category + expiry → employee context sees it
  under My documents → download (fresh URL resolves) → HR deletes → gone from
  both surfaces → a direct download request returns 404. Separate
  `browser.newContext()` per role (house pattern).
- `tests/e2e/full-hop.sh` / Makefile: MinIO (dev) is up before Playwright
  runs; the browser reaches `127.0.0.1:9000` (loopback ports + CORS from
  T2).

**Files:** `tests/e2e/documents.spec.ts` · `tests/e2e/fixtures/` ·
`tests/e2e/full-hop.sh` (only if the bring-up needs it) · `Makefile`.

**Verify:** `make check-e2e`, then `make check` end-to-end.

#### T9 — Record

**Changes**

- Tick every acceptance box above with its test reference; set
  **Status: done**; update `.ai/features/README.md` (backlog index) and
  `tasks/TODO.md`.
- Docs rows were already landed with the contracts (T1): `.ai/domain.md`
  event catalog × 2, `.ai/security.md` capability rows × 2.

**Files:** this spec · `.ai/features/README.md` · `tasks/TODO.md`.

**Verify:** `make check` green; `make gen-api` diff-free.

### 4. Test strategy

| Layer | Files | What it proves | Gate |
| --- | --- | --- | --- |
| Unit – schemas | `tests/unit/documents/test_schemas.py` | Bounds/enum/at-least-one; error code ↔ status pairs | `check-api` |
| Unit – policies | `test_policies.py` | Write matrix (HR/Admin/Super Admin only); read matrix (owner any role, manager self + direct reports one level, Finance own, Employee other → 403) | `check-api` |
| Unit – storage | `test_storage.py` | Presign policy pins exact key + claimed content-type/size + filename channel + `content-length-range`; upload TTL ≤ 10 min, download TTL ≤ 5 min | `check-api` |
| Unit – service | `fakes.py`, `test_service.py` | Intent 422s; confirm 409 with zero writes; confirm happy (audit + outbox payload = ids + category); update = audit + **no** outbox row; delete purge-first, second delete 404; `PII_CHANGED` sentinel | `check-api` |
| Integration – API | `test_documents_api.py` | AC 1/3/8/9 happy paths against real DB + MinIO-test (staged → final key, purge on delete) | `check-api` |
| Integration – failures | `test_documents_failures.py` | 422 type/size at intent **and** confirm re-checks; 409 confirm + replay; delete twice 404; deleted rows 404 on get/download | `check-api` |
| Integration – roles | `test_documents_roles.py` | AC 5/6/7 full matrix incl. manager self/report/non-report | `check-api` |
| Integration – tenancy | `test_documents_tenant_isolation.py` | AC 10: cross-tenant read/update/delete → `404 document_not_found`; foreign `employee_id` → `404 employee_not_found`; storage untouched | `check-api` |
| Integration – audit | `test_documents_audit.py` | `document.uploaded`/`updated`/`deleted` entries; PII field names only, never values | `check-api` |
| Contract | `test_documents_events.py` + outbox/catalog sweeps | Builders validate vs v1 schemas + envelope; PII-key sweep; message key = `employee_id`; OpenAPI drift after `make gen-api` | `check-api` |
| Web | `apps/web/tests/documents/*.test.tsx` | Loading/empty/error+retry; confirm-failure toast with pinned `document_upload_not_completed` copy (en + vi keys); expired indicator; write controls hidden per role | `check-web` |
| E2E | `tests/e2e/documents.spec.ts` | AC 16 full hop across two sessions incl. the post-delete 404 | `check-e2e` (inside `make check`) |

Failure cases are explicit per layer: 422 `document_type_not_allowed` /
`document_too_large` (intent + confirm), 409 `document_upload_not_completed`
(confirm without staging object, replay, missing protocol metadata), 403
`forbidden` (denied write, denied/out-of-scope read), 404
`document_not_found` / `employee_not_found` (foreign, unknown, deleted,
cross-tenant), second delete 404, and the web rendering of each pinned code
without leaking the raw code.

### 5. Risks, unknowns, decisions

1. **Filename/claim transport (spec gap).** The pinned `DocumentCreate`
   carries no filename and the AC requires `filename`/`content_type`/
   `size_bytes` to "match the staged object", so the plan pins an object
   metadata channel: the client sends `x-amz-meta-filename` (percent-encoded
   UTF-8, ≤ 255 decoded) as a POST form field, and the presigned policy pins
   `x-amz-meta-claimed-size = size_bytes` (from `DocumentUploadCreate`).
   Confirm reads both from the HEAD. Missing/undecodable metadata → 409
   `document_upload_not_completed` (the pinned protocol is not completed).
   Recorded in ADR 0022.
2. **Stateless upload intent (spec gap).** "An abandoned upload creates no
   row" rules out a staging-intent table, so the claim is enforced by the
   POST policy (`content-length-range 1..size_bytes`, `eq $Content-Type`)
   and re-checked at confirm via `x-amz-meta-claimed-size` (belt-and-
   braces, per the edge-case rule). Concurrent duplicate confirms have a
   tiny race window between copy and staging purge; the loser sees no staged
   object and gets 409. Accepted; ADR 0022 records it.
3. **Own-profile surface does not exist** (repo finding). The plan creates a
   minimal `/(dashboard)/profile` route whose v1 content is "My documents"
   (read-only) + a nav entry. Alternative rejected: hanging "My documents"
   off the HR employee-detail surface — the AC wants an own-only,
   no-write-control self surface.
4. **No tabs on the employee detail page yet.** T6 introduces a two-tab
   shell (Overview | Documents) around `EmployeeDetailCard`; this becomes
   the pattern for future tabs. Risk is low but it is a layout change to an
   existing page.
5. **`deleted_at` is a new convention** (the codebase only has
   `archived_at`), and second-delete `404` deliberately differs from the
   `archived_at` idempotent no-op. Spec-pinned; do not "fix" to the
   precedent.
6. **Storage/DB consistency.** Confirm order: stat → checks → copy → purge
   staging → row + audit + event in one DB transaction (a tx failure after
   the copy leaves an orphan final object — documented failure mode for a
   bucket lifecycle rule / ops; no row, no event). Delete order: purge the
   object first, then row + audit + event (a purge failure leaves the row,
   object and trail intact and the call retryable — never a half-deleted
   record). ADR 0022 records both.
7. **Employee-existence reader.** Prefer an existing adapter
   (`employee_usage.py` / `employee_link.py`) if it exposes in-tenant
   existence; otherwise add `EmployeeReferenceReader` (documents-local
   Protocol) + `PostgresEmployeeReferenceReader`. Decided at the keyboard in
   T3; no cross-context table reads either way (architecture rule 3 / the
   enrichment-port rule).
8. **New dependency: boto3** (sync, offloaded via `anyio.to_thread`).
   Justified in ADR 0022 (S3-compatible presigned-POST policy conditions;
   real S3 later changes no surface). If storage ever becomes a hot path,
   `aioboto3` swaps behind the `DocumentStorage` port with no API change.
9. **Dev/e2e orchestration.** MinIO must join `make dev`, `check-api` infra
   and `check-e2e` bring-up, browsers must reach `127.0.0.1:9000`, and CORS
   must allow the web origin. T2 owns compose/Makefile; T8 verifies the hop.
10. **Open decisions closed at approval:** 7 (field-level encryption deferred
    — recorded in ADR 0022 so compliance can reopen it) and 8 (this spec +
    `.ai/security.md` rows + ADR 0022 are the security-review record).
11. **ADRs:** 0022 only. No ADR 0012 amendment — both events carry
    `employee_id`, which already heads the message-key fallback.

### 6. Definition of done

- Every acceptance box above is ticked with a test reference.
- `make check` green (API + web + e2e), `make gen-api` diff-free.
- Two v1 contracts in `contracts/events/documents/`; `.ai/domain.md` catalog
  rows and `.ai/security.md` capability rows landed (T1); ADR 0022 recorded.
- Audit entries on upload, metadata update and delete; no PII (`title`,
  `filename`, storage keys, presigned URLs) in logs, errors, audit values or
  event payloads; tenant isolation proven with storage untouched.
- Feature spec `Status: done`; `.ai/features/README.md` and `tasks/TODO.md`
  updated in the same change.
