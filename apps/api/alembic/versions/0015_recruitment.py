"""recruitment — jobs, candidates, interviews

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-23 10:30:00.000000

Creates the three Phase-4 recruitment aggregates in one migration (one flow,
one persistence change): `jobs` (`draft → open → closed`), `candidates` (one
application per `(tenant, job, email)`, pipeline state machine) and
`interviews` (scheduled conversations with an employee interviewer).

Statuses are lowercase stored values with DB check constraints; the candidate
email uniqueness is exact per risk 5 in the feature spec. Indexes are
tenant-first; the interview indexes cover `?mine=1` reads and the
interview-derived visibility of jobs/candidates. Constraint and index names
match the models' `__table_args__` (parity rule).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=5000), nullable=True),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("openings", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            "openings >= 1",
            name=op.f("ck_jobs_openings_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'open', 'closed')",
            name=op.f("ck_jobs_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_jobs_department_id_departments"),
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            name=op.f("fk_jobs_position_id_positions"),
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name=op.f("fk_jobs_location_id_locations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "ix_jobs_tenant_status_created",
        "jobs",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_jobs_tenant_department",
        "jobs",
        ["tenant_id", "department_id"],
        unique=False,
    )

    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(length=200), nullable=False),
        sa.Column("last_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stage_note", sa.String(length=500), nullable=True),
        sa.Column("rejected_from", sa.String(length=20), nullable=True),
        sa.Column("hired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("employee_id", sa.Uuid(), nullable=True),
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
            "status IN ('applied', 'interviewing', 'offered', 'hired', "
            "'rejected', 'withdrawn')",
            name=op.f("ck_candidates_status_valid"),
        ),
        sa.CheckConstraint(
            "source IN ('referral', 'job_board', 'agency', 'other')",
            name=op.f("ck_candidates_source_valid"),
        ),
        sa.CheckConstraint(
            "rejected_from IS NULL OR rejected_from IN "
            "('applied', 'interviewing', 'offered')",
            name=op.f("ck_candidates_rejected_from_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_candidates_job_id_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_candidates_employee_id_employees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidates")),
        sa.UniqueConstraint(
            "tenant_id",
            "job_id",
            "email",
            name="uq_candidates_tenant_job_email",
        ),
    )
    op.create_index(
        "ix_candidates_tenant_job_status",
        "candidates",
        ["tenant_id", "job_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_candidates_tenant_status_created",
        "candidates",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "interviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("interviewer_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("feedback_notes", sa.String(length=1000), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('scheduled', 'completed', 'cancelled')",
            name=op.f("ck_interviews_status_valid"),
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name=op.f("ck_interviews_rating_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name=op.f("fk_interviews_candidate_id_candidates"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_interviews_job_id_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["interviewer_id"],
            ["employees.id"],
            name=op.f("fk_interviews_interviewer_id_employees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interviews")),
    )
    op.create_index(
        "ix_interviews_tenant_candidate_status",
        "interviews",
        ["tenant_id", "candidate_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_interviews_tenant_interviewer_scheduled",
        "interviews",
        ["tenant_id", "interviewer_id", "scheduled_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_interviews_tenant_interviewer_scheduled",
        table_name="interviews",
    )
    op.drop_index(
        "ix_interviews_tenant_candidate_status",
        table_name="interviews",
    )
    op.drop_table("interviews")
    op.drop_index("ix_candidates_tenant_status_created", table_name="candidates")
    op.drop_index("ix_candidates_tenant_job_status", table_name="candidates")
    op.drop_table("candidates")
    op.drop_index("ix_jobs_tenant_department", table_name="jobs")
    op.drop_index("ix_jobs_tenant_status_created", table_name="jobs")
    op.drop_table("jobs")
