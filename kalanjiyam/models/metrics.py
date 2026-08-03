"""Models for tracking system metrics, background queue tasks, latencies, and error logs."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy import Text as _Text
from sqlalchemy.orm import relationship

from kalanjiyam.models.base import Base, pk

__all__ = ["SystemMetricLog"]


class SystemMetricLog(Base):
    """Logs background task statuses, execution latencies, and application error logs."""

    __tablename__ = "system_metric_logs"

    id = pk()
    # 'queue', 'latency', 'error'
    category = Column(String, nullable=False, index=True)
    # Task name, route name, exception name, or service name
    name = Column(String, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True, index=True)
    # 'RUNNING', 'SUCCESS', 'FAILED', 'PENDING'
    status = Column(String, nullable=True, index=True)
    # Latency / duration in milliseconds
    latency_ms = Column(Float, nullable=True)
    # OpenTelemetry trace correlation ID
    trace_id = Column(String, nullable=True, index=True)
    # 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    error_level = Column(String, nullable=True, index=True)
    error_message = Column(_Text, nullable=True)
    traceback = Column(_Text, nullable=True)
    # Extra JSON context or metadata (queue_name, task_id, request_path, etc.)
    details = Column(_Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = relationship("User")
    group = relationship("Group")

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "name": self.name,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "group_id": self.group_id,
            "group_name": self.group.name if self.group else None,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "trace_id": self.trace_id,
            "error_level": self.error_level,
            "error_message": self.error_message,
            "traceback": self.traceback,
            "details": self.details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
