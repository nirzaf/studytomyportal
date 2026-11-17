"""Private retention of the native responses cleared by a managed retry."""

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel


class StudyAssessmentResponseArchive(SQLModel, table=True):
    __tablename__ = "st_assessment_response_archive"

    round_uuid: str = Field(primary_key=True, max_length=64)
    org_id: int = Field(foreign_key="organization.id", ondelete="RESTRICT")
    user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    policy_version_id: int = Field(
        foreign_key="st_policy_version.id", ondelete="RESTRICT"
    )
    content_digest: str = Field(max_length=64)
    responses: list = Field(sa_column=Column(JSON, nullable=False))
    retain_until: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
