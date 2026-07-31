from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime

from kalanjiyam.models.base import Base

__all__ = ['BatchJob', 'BatchItem', 'BatchOcrChunk', 'BatchOcrPage']

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
    chunks = relationship('BatchOcrChunk', back_populates='item', cascade='all, delete-orphan')
    ocr_pages = relationship('BatchOcrPage', back_populates='item', cascade='all, delete-orphan')

class BatchOcrChunk(Base):
    __tablename__ = 'batch_ocr_chunks'
    __table_args__ = (
        UniqueConstraint('batch_item_id', 'start_page', 'end_page', name='uq_batch_ocr_chunk_range'),
    )

    id = Column(Integer, primary_key=True)
    batch_item_id = Column(Integer, ForeignKey('batch_items.id', ondelete='CASCADE'), nullable=False, index=True)
    start_page = Column(Integer, nullable=False)
    end_page = Column(Integer, nullable=False)
    status = Column(String(64), nullable=False, default='PENDING') # PENDING, IN_PROGRESS, COMPLETED, FAILED
    attempt_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    total_ocr_latency_ms = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    item = relationship('BatchItem', back_populates='chunks')
    pages = relationship('BatchOcrPage', back_populates='chunk', cascade='all, delete-orphan')

class BatchOcrPage(Base):
    __tablename__ = 'batch_ocr_pages'
    __table_args__ = (
        UniqueConstraint('chunk_id', 'page_number', name='uq_batch_ocr_page_number'),
    )

    id = Column(Integer, primary_key=True)
    chunk_id = Column(Integer, ForeignKey('batch_ocr_chunks.id', ondelete='CASCADE'), nullable=False, index=True)
    batch_item_id = Column(Integer, ForeignKey('batch_items.id', ondelete='CASCADE'), nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    status = Column(String(64), nullable=False, default='PENDING') # PENDING, IN_PROGRESS, COMPLETED, FAILED
    attempt_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    chunk = relationship('BatchOcrChunk', back_populates='pages')
    item = relationship('BatchItem', back_populates='ocr_pages')
