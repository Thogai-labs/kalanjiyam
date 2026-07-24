"""Various site content unrelated to texts and proofing.

The idea is that a trusted user can edit site content by creating and modifyng
these objects. By doing so, they can update the site without waiting for a site
deploy.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy import Text as Text_
from sqlalchemy.orm import relationship

from kalanjiyam.models.base import Base, pk


class ProjectSponsorship(Base):

    """A project that a donor can sponsor."""

    __tablename__ = "site_project_sponsorship"

    #: Primary key.
    id = pk()
    #: Sanskrit title.
    sa_title = Column(String, nullable=False)
    #: English title.
    en_title = Column(String, nullable=False)
    #: A short description of this project.
    description = Column(Text_, nullable=False)
    #: The estimated cost of this project in Indian rupees (INR).
    cost_inr = Column(Integer, nullable=False)


class ContributorInfo(Base):

    """Information about a Kalanjiyam contributor.

    For now, we use this for just proofreaders. Long-term, we might include
    other types of contributors here as well.
    """

    __tablename__ = "contributor_info"

    #: Primary key.
    id = pk()
    #: The contributor's name.
    name = Column(String, nullable=False)
    #: The contributor's title, role, occupation, etc.
    title = Column(String, nullable=False, default="")
    #: A short description of this proofer.
    description = Column(Text_, nullable=False, default="")


class UsageLog(Base):
    """Logs high-level user actions for analytics and rate-limiting."""

    __tablename__ = "usage_logs"

    id = pk()
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    fingerprint_id = Column(String, nullable=True, index=True)
    ip_address = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False, index=True)  # "create_project" or "run_ocr"
    project_slug = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User")


class ReportedIssue(Base):
    """User-submitted issues and bug reports from the Contact page."""

    __tablename__ = "reported_issues"

    id = pk()
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    category = Column(String, nullable=False)
    message = Column(Text_, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending, resolved, not_applicable
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User")


