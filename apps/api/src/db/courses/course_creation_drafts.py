from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

COURSE_DRAFT_MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024


class CourseCreationDraft(SQLModel, table=True):
    """One unfinished "Create Course" form per (organization, user).

    This is deliberately not a ``Course`` row: an incomplete form must not be
    counted against plan limits, listed, or visible to anyone but its owner.
    ``revision`` is the optimistic-concurrency token; every write must present
    the revision it last saw, so a stale second tab cannot silently overwrite.
    Optional thumbnail bytes are stored in the configured private content store
    under ``orgs/{org_uuid}/studytomy/course-drafts/{draft_uuid}/{filename}``
    (which ``is_private_studytomy_path`` blocks from generic ``/content/`` routes)
    and served only through the owner-checked draft thumbnail endpoint.
    """

    __tablename__ = "course_creation_draft"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_course_creation_draft_owner"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    draft_uuid: str = Field(sa_column=Column(String, unique=True, nullable=False))
    org_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    user_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    name: str = Field(default="", sa_column=Column(String, nullable=False, server_default=""))
    description: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    learnings: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    tags: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    public: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="true"))
    thumbnail_filename: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    thumbnail_content_type: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    revision: int = Field(default=1, sa_column=Column(Integer, nullable=False, server_default="1"))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CourseDraftFields(BaseModel):
    """Writable form fields. Incomplete values are valid; only bounds apply."""

    model_config = ConfigDict(extra="forbid")

    name: str = PydanticField(default="", max_length=100)
    description: str = PydanticField(default="", max_length=1000)
    learnings: str = PydanticField(default="", max_length=10000)
    tags: str = PydanticField(default="", max_length=10000)
    public: bool = True
    # None/0 means "I believe no draft exists"; otherwise the revision last seen.
    base_revision: Optional[int] = PydanticField(default=None, ge=0)


class CourseDraftRead(BaseModel):
    draft_uuid: str
    name: str
    description: str
    learnings: str
    tags: str
    public: bool
    has_thumbnail: bool
    thumbnail_content_type: Optional[str] = None
    revision: int
    created_at: datetime
    updated_at: datetime
