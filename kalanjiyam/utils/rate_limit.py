"""Rate limiting and IP/Fingerprint tracking utility."""

import functools
from datetime import datetime, timedelta
from flask import flash, redirect, request, url_for
from flask_login import current_user

import kalanjiyam.database as db
import kalanjiyam.queries as q


def is_rate_limited(
    action: str,
    ip_address: str,
    fingerprint_id: str | None = None,
    user_id: int | None = None,
    limit: int = 5,
    period_seconds: int = 86400
) -> bool:
    """
    Check if the number of occurrences of `action` in the last `period_seconds`
    exceeds `limit` for the given IP address, fingerprint ID, or user ID.
    
    If any of these (IP, fingerprint, or user_id) exceed the limit, it returns True.
    """
    session = q.get_session()
    cutoff = datetime.utcnow() - timedelta(seconds=period_seconds)
    
    # Query logs matching the action and created after the cutoff
    base_query = session.query(db.UsageLog).filter(
        db.UsageLog.action == action,
        db.UsageLog.created_at >= cutoff
    )
    
    # Check IP address limit
    ip_count = base_query.filter(db.UsageLog.ip_address == ip_address).count()
    if ip_count >= limit:
        return True
        
    # Check fingerprint ID limit
    if fingerprint_id:
        fp_count = base_query.filter(db.UsageLog.fingerprint_id == fingerprint_id).count()
        if fp_count >= limit:
            return True
            
    # Check user ID limit
    if user_id:
        user_count = base_query.filter(db.UsageLog.user_id == user_id).count()
        if user_count >= limit:
            return True
            
    return False


def log_usage_action(
    action: str,
    ip_address: str,
    fingerprint_id: str | None = None,
    user_id: int | None = None,
    project_slug: str | None = None
) -> db.UsageLog:
    """Log an action usage in the database."""
    session = q.get_session()
    log_entry = db.UsageLog(
        user_id=user_id,
        fingerprint_id=fingerprint_id,
        ip_address=ip_address,
        action=action,
        project_slug=project_slug,
        created_at=datetime.utcnow()
    )
    session.add(log_entry)
    session.commit()
    return log_entry


def ratelimit(
    action: str,
    limit: int = 5,
    period_seconds: int = 86400,
    redirect_endpoint: str = "proofing.index",
    only_guests: bool = True
):
    """
    Flask route decorator for rate limiting.
    If only_guests=True, registered users are completely exempt from this rate limit.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Exempt registered users if only_guests is True
            if only_guests and current_user.is_authenticated:
                return func(*args, **kwargs)
                
            ip_address = request.remote_addr
            fingerprint_id = request.cookies.get("device_fingerprint")
            user_id = current_user.id if current_user.is_authenticated else None
            
            if is_rate_limited(
                action=action,
                ip_address=ip_address,
                fingerprint_id=fingerprint_id,
                user_id=user_id,
                limit=limit,
                period_seconds=period_seconds
            ):
                flash("Rate limit exceeded for this action. Please try again later.", "error")
                return redirect(url_for(redirect_endpoint))
                
            return func(*args, **kwargs)
        return wrapper
    return decorator
