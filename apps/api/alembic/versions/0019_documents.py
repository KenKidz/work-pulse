"""documents — employee document metadata

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-29 09:00:00.000000

Creates the `document` table: metadata only, one row per employee file whose
bytes live in private object storage (ADR 0022). `deleted_at` is the soft-delete
marker (ADR 0007's spirit) — deleted rows are kept for traceability while the
stored object is purged, and visible reads filter them out. `category` is the
pinned lowercase enum with a DB check constraint; `size_bytes` carries the
pinned 20 MB backstop.

The list index is partial on live rows (`deleted_at IS NULL`) and ordered
`created_at DESC`, matching the employee Documents tab; it is declared in both
the model's `__table_args__` and here (model/migration parity rule).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('contract', 'identification', 'certification', 'other')",
            name=op.f("ck_document_category_valid"),
        ),
        sa.CheckConstraint(
            "size_bytes >= 1 AND size_bytes <= 20971520",
            name=op.f("ck_document_size_bytes_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_document_employee_id_employees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
    )
    op.create_index(
        "ix_document_tenant_employee_created",
        "document",
        ["tenant_id", "employee_id", sa.text("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
        postgresql_include=["id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_tenant_employee_created",
        table_name="document",
    )
    op.drop_table("document")
