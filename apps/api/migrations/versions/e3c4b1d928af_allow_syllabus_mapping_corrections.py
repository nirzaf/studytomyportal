"""Allow a corrected mapping after the prior mapping is retired."""

from alembic import op
import sqlalchemy as sa


revision = "e3c4b1d928af"
down_revision = "d9f74c2a81e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_st_syllabus_mapping_evidence",
        "st_syllabus_concept_mapping",
        type_="unique",
    )
    op.create_index(
        "uq_st_syllabus_mapping_active_evidence",
        "st_syllabus_concept_mapping",
        ["source_version_id", "curriculum_revision_id", "concept_id", "page_start", "page_end", "evidence_kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_review', 'approved')"),
    )


def downgrade() -> None:
    duplicate = op.get_bind().execute(sa.text(
        """
        SELECT 1 FROM st_syllabus_concept_mapping
        GROUP BY source_version_id, curriculum_revision_id, concept_id, page_start, page_end, evidence_kind
        HAVING count(*) > 1
        LIMIT 1
        """
    )).first()
    if duplicate:
        raise RuntimeError(
            "Cannot restore the original global mapping uniqueness constraint while historical corrections share a mapping key."
        )
    op.drop_index("uq_st_syllabus_mapping_active_evidence", table_name="st_syllabus_concept_mapping")
    op.create_unique_constraint(
        "uq_st_syllabus_mapping_evidence",
        "st_syllabus_concept_mapping",
        ["source_version_id", "curriculum_revision_id", "concept_id", "page_start", "page_end", "evidence_kind"],
    )
