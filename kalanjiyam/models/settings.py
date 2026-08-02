"""Model for global platform/system settings."""

from sqlalchemy import BigInteger, Column, Integer, String

from kalanjiyam.models.base import Base, pk


class SystemSetting(Base):
    """Global system/platform settings for user limits."""

    __tablename__ = "system_settings"

    id = pk()
    
    # Unregistered users (guests)
    unregistered_user_ocr_limit = Column(Integer, nullable=False, default=10)
    unregistered_user_project_limit = Column(Integer, nullable=False, default=5)
    unregistered_user_upload_limit = Column(Integer, nullable=False, default=10)

    default_ocr_engine = Column(String, nullable=False, default="tesseract")
    recommended_ocr_engine = Column(String, nullable=True)

    # File storage cleanup
    auto_cleanup_days = Column(Integer, nullable=False, default=7)
