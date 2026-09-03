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

    default_translation_engine = Column(String, nullable=False, default="indictrans2")
    recommended_translation_engine = Column(String, nullable=True)

    @property
    def default_translation_model(self):
        return self.default_translation_engine

    @default_translation_model.setter
    def default_translation_model(self, val):
        self.default_translation_engine = val

    @property
    def recommended_translation_model(self):
        return self.recommended_translation_engine

    @recommended_translation_model.setter
    def recommended_translation_model(self, val):
        self.recommended_translation_engine = val

    @property
    def best_translation_model(self):
        return self.recommended_translation_engine

    @best_translation_model.setter
    def best_translation_model(self, val):
        self.recommended_translation_engine = val

    # File storage cleanup
    auto_cleanup_days = Column(Integer, nullable=False, default=7)

    def __init__(self, **kwargs):
        kwargs.setdefault("default_ocr_engine", "tesseract")
        kwargs.setdefault("default_translation_engine", "indictrans2")
        kwargs.setdefault("unregistered_user_ocr_limit", 10)
        kwargs.setdefault("unregistered_user_project_limit", 5)
        kwargs.setdefault("unregistered_user_upload_limit", 10)
        kwargs.setdefault("auto_cleanup_days", 7)
        super().__init__(**kwargs)
