"""Documents domain — employee document metadata and private object storage.

Spec: `.ai/features/employee-documents.md`. The domain owns the two-step upload
protocol, the read/write policies, audit entries and the two identifiers-only
events. The bytes live in private object storage behind a `DocumentStorage`
port (T2); this domain never imports an S3/MinIO client (architecture rule 3).
Postgres and MinIO adapters live in `app.infrastructure`.
"""
