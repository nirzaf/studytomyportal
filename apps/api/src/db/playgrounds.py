from typing import Optional
from enum import Enum
from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Text
from sqlmodel import Field, SQLModel

from src.services.playgrounds.schemas.playgrounds_generator import LearnerAdaptationContext


class PlaygroundAccessType(str, Enum):
    PUBLIC = "public"               # Anonymous users can view
    AUTHENTICATED = "authenticated"  # Must be logged in
    RESTRICTED = "restricted"        # User groups only


class PlaygroundBase(SQLModel):
    name: str
    description: Optional[str] = None
    thumbnail_image: Optional[str] = None
    access_type: PlaygroundAccessType = PlaygroundAccessType.AUTHENTICATED
    published: bool = False
    course_uuid: Optional[str] = None  # Optional course link (for RAG)
    html_content: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    learner_adaptation: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )


class Playground(PlaygroundBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    )
    playground_uuid: str = Field(default="", index=True)
    thumbnail_content_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column(String(length=64), nullable=True),
    )
    course_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("course.id", ondelete="SET NULL"), nullable=True),
    )
    created_by: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    creation_date: str = ""
    update_date: str = ""


class PlaygroundRead(PlaygroundBase):
    id: int
    org_id: int
    org_uuid: Optional[str] = None
    org_slug: Optional[str] = None
    playground_uuid: str
    course_id: Optional[int] = None
    created_by: Optional[int] = None
    author_username: Optional[str] = None
    author_first_name: Optional[str] = None
    author_last_name: Optional[str] = None
    author_user_uuid: Optional[str] = None
    author_avatar_image: Optional[str] = None
    creation_date: str
    update_date: str


class PlaygroundCreate(SQLModel):
    name: str
    description: Optional[str] = None
    thumbnail_image: Optional[str] = None
    access_type: PlaygroundAccessType = PlaygroundAccessType.AUTHENTICATED
    course_uuid: Optional[str] = None
    html_content: Optional[str] = None
    learner_adaptation: Optional[LearnerAdaptationContext] = None


class PlaygroundUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    thumbnail_image: Optional[str] = None
    access_type: Optional[PlaygroundAccessType] = None
    published: Optional[bool] = None
    course_uuid: Optional[str] = None
    html_content: Optional[str] = None
    learner_adaptation: Optional[LearnerAdaptationContext] = None
