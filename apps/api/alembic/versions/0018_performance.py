"""performance — goals, reviews, feedback, skills

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-26 11:20:00.000000

Creates the six Performance tables in one migration (one flow, one persistence
change): `performance_goals` (`draft → active → completed | cancelled`),
`performance_reviews` (`draft → completed | cancelled`), the
`performance_review_providers` nomination join, immutable
`performance_review_feedback`, the per-tenant `performance_skills` catalog
(ADR 0007 soft delete via `archived_at`) and `performance_employee_skills`
(one row per `(employee_id, skill_id)`).

Statuses are lowercase stored values with DB check constraints; skill name
uniqueness spans archived rows; feedback uniqueness is
`(review_id, author_id)`. Indexes are tenant-first and match the models'
`__table_args__` (model/migration parity rule).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "performance_goals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "progress_pct",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'cancelled')",
            name=op.f("ck_performance_goals_status_valid"),
        ),
        sa.CheckConstraint(
            "progress_pct >= 0 AND progress_pct <= 100",
            name=op.f("ck_performance_goals_progress_pct_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_performance_goals_employee_id_employees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_performance_goals")),
    )
    op.create_index(
        "ix_performance_goals_tenant_employee_status_created",
        "performance_goals",
        ["tenant_id", "employee_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_performance_goals_tenant_created",
        "performance_goals",
        ["tenant_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "performance_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.String(length=50), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("strengths", sa.String(length=2000), nullable=True),
        sa.Column("improvements", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('draft', 'completed', 'cancelled')",
            name=op.f("ck_performance_reviews_status_valid"),
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name=op.f("ck_performance_reviews_rating_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_performance_reviews_employee_id_employees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_performance_reviews")),
    )
    op.create_index(
        "ix_performance_reviews_tenant_employee_status_created",
        "performance_reviews",
        ["tenant_id", "employee_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_performance_reviews_tenant_author_status_created",
        "performance_reviews",
        ["tenant_id", "author_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_performance_reviews_tenant_created",
        "performance_reviews",
        ["tenant_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "performance_review_providers",
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["performance_reviews.id"],
            name=op.f("fk_performance_review_providers_review_id_performance_reviews"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_performance_review_providers_employee_id_employees"),
        ),
        sa.PrimaryKeyConstraint(
            "review_id",
            "employee_id",
            name=op.f("pk_performance_review_providers"),
        ),
    )
    op.create_index(
        "ix_performance_review_providers_tenant_employee",
        "performance_review_providers",
        ["tenant_id", "employee_id", "review_id"],
        unique=False,
    )

    op.create_table(
        "performance_review_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("comment", sa.String(length=2000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["performance_reviews.id"],
            name=op.f("fk_performance_review_feedback_review_id_performance_reviews"),
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["employees.id"],
            name=op.f("fk_performance_review_feedback_author_id_employees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_performance_review_feedback")),
        sa.UniqueConstraint(
            "review_id",
            "author_id",
            name="uq_performance_review_feedback_review_author",
        ),
    )
    op.create_index(
        "ix_performance_review_feedback_tenant_review_created",
        "performance_review_feedback",
        ["tenant_id", "review_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "performance_skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_performance_skills")),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_performance_skills_tenant_name",
        ),
    )

    op.create_table(
        "performance_employee_skills",
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("proficiency", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
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
        sa.CheckConstraint(
            "proficiency >= 1 AND proficiency <= 5",
            name=op.f("ck_performance_employee_skills_proficiency_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_performance_employee_skills_employee_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["performance_skills.id"],
            name=op.f("fk_performance_employee_skills_skill_id_performance_skills"),
        ),
        sa.PrimaryKeyConstraint(
            "employee_id",
            "skill_id",
            name=op.f("pk_performance_employee_skills"),
        ),
    )
    op.create_index(
        "ix_performance_employee_skills_tenant_employee",
        "performance_employee_skills",
        ["tenant_id", "employee_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_performance_employee_skills_tenant_employee",
        table_name="performance_employee_skills",
    )
    op.drop_table("performance_employee_skills")
    op.drop_table("performance_skills")
    op.drop_index(
        "ix_performance_review_feedback_tenant_review_created",
        table_name="performance_review_feedback",
    )
    op.drop_table("performance_review_feedback")
    op.drop_index(
        "ix_performance_review_providers_tenant_employee",
        table_name="performance_review_providers",
    )
    op.drop_table("performance_review_providers")
    op.drop_index(
        "ix_performance_reviews_tenant_created",
        table_name="performance_reviews",
    )
    op.drop_index(
        "ix_performance_reviews_tenant_author_status_created",
        table_name="performance_reviews",
    )
    op.drop_index(
        "ix_performance_reviews_tenant_employee_status_created",
        table_name="performance_reviews",
    )
    op.drop_table("performance_reviews")
    op.drop_index(
        "ix_performance_goals_tenant_created",
        table_name="performance_goals",
    )
    op.drop_index(
        "ix_performance_goals_tenant_employee_status_created",
        table_name="performance_goals",
    )
    op.drop_table("performance_goals")
