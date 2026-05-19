"""Models for access groups: users and content (texts/projects) per group."""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy import Text as Text_
from sqlalchemy.orm import relationship

from kalanjiyam.models.base import Base, foreign_key, pk


class Group(Base):
    """A group that can be assigned users and content (texts/projects)."""

    __tablename__ = "groups"

    #: Primary key.
    id = pk()
    #: Human-readable name.
    name = Column(String, nullable=False)
    #: Optional description.
    description = Column(Text_, nullable=False, default="")

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
