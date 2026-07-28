from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import relationship
from datetime import datetime

from kalanjiyam.models.base import Base

__all__ = ['BatchJob', 'BatchItem']

class BatchJob(Base):
    __tablename__ = 'batch_jobs'

    id = Column(Integer, primary_key=True)
    target_uri = Column(String(1024), nullable=False)
    status = Column(String(64), nullable=False, default='PENDING') # PENDING, IN_PROGRESS, COMPLETED, FAILED
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    items = relationship('BatchItem', back_populates='job', cascade='all, delete-orphan')

class BatchItem(Base):
    __tablename__ = 'batch_items'

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('batch_jobs.id'), nullable=False)
    file_path = Column(String(1024), nullable=False)
    mime_type = Column(String(128), nullable=True)
    project_id = Column(Integer, ForeignKey('proof_projects.id', ondelete='SET NULL'), nullable=True)
    
    status = Column(String(64), nullable=False, default='PENDING') # PENDING, DOWNLOADED, IMAGES_EXTRACTED, OCR_IN_PROGRESS, COMPLETED, FAILED
    
    # Metrics
    source_size_bytes = Column(Integer, nullable=True)
    extraction_latency_ms = Column(Float, nullable=True)
    total_ocr_latency_ms = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    job = relationship('BatchJob', back_populates='items')
    project = relationship('Project')
