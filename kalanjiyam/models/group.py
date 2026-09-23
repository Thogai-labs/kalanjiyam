"""Models for access groups / organizations and their memberships."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy import Text as Text_
from sqlalchemy.orm import relationship

from kalanjiyam.models.base import Base, foreign_key, pk


class Group(Base):
    """An organization/group that can be assigned users and content."""

    __tablename__ = "groups"

    #: Primary key.
    id = pk()
    #: Human-readable name.
    name = Column(String, nullable=False)
    #: URL-safe unique identifier.
    slug = Column(String, nullable=False, unique=True, index=True)
    #: Optional description.
    description = Column(Text_, nullable=False, default="")
    #: Current organization status.
    is_active = Column(Boolean, nullable=False, default=True)
    #: Optional storage quota in bytes. Null means unlimited.
    storage_quota_bytes = Column(BigInteger, nullable=True)
    #: Cached storage usage in bytes.
    storage_used_bytes = Column(BigInteger, nullable=False, default=0)
    #: Optional OCR credit limit. Null means unlimited.
    ocr_credit_limit = Column(Integer, nullable=True)
    #: OCR credits consumed.
    ocr_credits_used = Column(Integer, nullable=False, default=0)
    #: Optional translation credit limit. Null means unlimited.
    translation_credit_limit = Column(Integer, nullable=True)
    #: Translation credits consumed.
    translation_credits_used = Column(Integer, nullable=False, default=0)
    #: Optional per-user storage quota in bytes. Null means unlimited/no personal quota.
    default_user_storage_limit = Column(BigInteger, nullable=True)
    #: Optional per-user OCR credit limit. Null means unlimited/no personal quota.
    default_user_ocr_limit = Column(Integer, nullable=True)
    #: Optional per-user translation credit limit. Null means unlimited/no personal quota.
    default_user_translation_limit = Column(Integer, nullable=True)
    #: Whether this org uses a dedicated S3 bucket instead of the shared
    #: platform bucket.  When False (default), files live in the platform
    #: bucket under the ``projects/{org_slug}/`` prefix.
    has_custom_storage = Column(Boolean, nullable=False, default=False)
    #: Dedicated S3 bucket name.  Auto-generated as ``org-{slug}`` when the
    #: admin enables custom storage but leaves this blank.
    s3_bucket = Column(String(63), nullable=True)
    #: Optional external S3 endpoint URL.  When blank, the platform's own
    #: VersityGW / S3 endpoint is used.
    s3_endpoint_url = Column(String, nullable=True)
    #: Optional S3 region for external buckets.
    s3_region = Column(String(32), nullable=True)
    #: Optional AWS access key for an external bucket.
    s3_access_key_id = Column(String, nullable=True)
    #: Optional AWS secret key for an external bucket.
    s3_secret_access_key = Column(String, nullable=True)
    #: Optional user designated as organization admin.
    admin_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    #: Timestamps.
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, nullable=False, onupdate=datetime.utcnow
    )

    #: Users in this group (many-to-many).
    users = relationship(
        "User",
        secondary="user_groups",
        backref="groups",
    )
    #: Library texts (books) visible to this group (many-to-many).
    texts = relationship(
        "Text",
        secondary="text_groups",
        backref="groups",
    )
    #: Proofing projects (books) visible to this group (many-to-many).
    projects = relationship(
        "Project",
        secondary="project_groups",
        backref="groups",
    )
    admin_user = relationship("User", foreign_keys=[admin_user_id])

    def __str__(self):
        return self.name


class UserGroups(Base):
    """Secondary table for users and groups (many-to-many)."""

    __tablename__ = "user_groups"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), primary_key=True, index=True)


class TextGroups(Base):
    """Secondary table for groups and texts (many-to-many)."""

    __tablename__ = "text_groups"

    group_id = Column(Integer, ForeignKey("groups.id"), primary_key=True, index=True)
    text_id = Column(Integer, ForeignKey("texts.id"), primary_key=True, index=True)


class ProjectGroups(Base):
    """Secondary table for groups and proofing projects (many-to-many)."""

    __tablename__ = "project_groups"

    group_id = Column(Integer, ForeignKey("groups.id"), primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("proof_projects.id"), primary_key=True, index=True
    )
