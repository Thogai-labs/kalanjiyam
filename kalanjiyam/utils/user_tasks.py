import os
import json
import redis
import logging
from datetime import datetime
from celery.result import GroupResult, AsyncResult
from kalanjiyam.tasks import app as celery_app

LOG = logging.getLogger(__name__)

# Initialize Redis client safely
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def safe_decode(val):
    if isinstance(val, bytes):
        return val.decode('utf-8')
    return val


def get_user_identifier(user, request=None):
    """
    Resolve a unique tracking key for the user.
    Returns:
        - "user:<user_id>" for authenticated users.
        - "guest:<fingerprint>" for guest/unregistered users.
        - None if neither is available.
    """
    try:
        if user and hasattr(user, "is_authenticated") and user.is_authenticated:
            return f"user:{user.id}"
        elif request:
            fingerprint = request.cookies.get("device_fingerprint")
            if fingerprint:
                return f"guest:{fingerprint}"
    except Exception as e:
        LOG.warning(f"Error resolving user identifier: {e}")
    return None


def add_user_task(user_identifier, task_id, task_type, project_slug, project_title, extra_info=None):
    """
    Logs a new background task under the user's task history in Redis.
    The task history is stored as a Redis hash table and expires after 7 days.
    """
    if not user_identifier or not task_id:
        return False
        
    try:
        task_info = {
            "task_id": task_id,
            "type": task_type,
            "project_slug": project_slug,
            "project_title": project_title,
            "started_at": datetime.utcnow().isoformat(),
            "status": "pending",
            "progress": 0.0,
            "completed_count": 0,
            "total_count": 0,
            "extra_info": extra_info or {}
        }
        
        key = f"user_tasks:{user_identifier}"
        redis_client.hset(key, task_id, json.dumps(task_info))
        redis_client.expire(key, 86400 * 7)  # Keep task history for 7 days
        return True
    except Exception as e:
        LOG.warning(f"Error adding task to user task list in Redis: {e}")
        return False


def get_user_tasks(user_identifier):
    """
    Fetches the list of background tasks for the given user, queries active
    tasks from Celery to update their progress and status, and caches
    completed/failed results back in Redis to save future Celery queries.
    """
    if not user_identifier:
        return []
        
    key = f"user_tasks:{user_identifier}"
    try:
        tasks_data = redis_client.hgetall(key)
    except Exception as e:
        LOG.warning(f"Error fetching user tasks from Redis: {e}")
        return []
        
    tasks = []
    updated_entries = {}
    
    for task_id_bytes, info_bytes in tasks_data.items():
        try:
            task_id = safe_decode(task_id_bytes)
            info = json.loads(safe_decode(info_bytes))
        except Exception as e:
            LOG.warning(f"Error parsing user task info from Redis: {e}")
            continue
            
        status = info.get('status', 'pending')
        task_type = info.get('type')
        
        # If task is not already in a terminal state (completed or failed),
        # query Celery to update progress/status.
        if status in ['pending', 'running']:
            try:
                if task_type in ['ocr', 'enhanced_ocr', 'translation']:
                    # Group result task checking
                    r = GroupResult.restore(task_id, app=celery_app)
                    if r and r.results:
                        current = r.completed_count()
                        total = len(r.results)
                        failed = sum(1 for result in r.results if result.failed())
                        
                        if current == total:
                            info['status'] = 'completed'
                            info['progress'] = 1.0
                            info['completed_count'] = current
                            info['total_count'] = total
                            updated_entries[task_id] = json.dumps(info)
                        else:
                            started_at_str = info.get('started_at')
                            is_stale = False
                            if started_at_str:
                                try:
                                    started_at = datetime.fromisoformat(started_at_str)
                                    if (datetime.utcnow() - started_at).total_seconds() > 3600:
                                        is_stale = True
                                except Exception:
                                    pass
                            
                            if is_stale or r.ready():
                                info['status'] = 'completed' if current > 0 else 'failed'
                                info['progress'] = 1.0
                                info['completed_count'] = current
                                info['total_count'] = total
                                updated_entries[task_id] = json.dumps(info)
                            else:
                                info['status'] = 'running'
                                info['progress'] = current / total if total > 0 else 0
                                info['completed_count'] = current
                                info['total_count'] = total
                                if failed > 0:
                                    info['failed_count'] = failed
                    else:
                        # Fallback for un-restorable task groups: check database BatchItem/BatchOcrPage
                        db_updated = False
                        try:
                            from kalanjiyam import queries as q
                            from kalanjiyam.models.batch import BatchItem, BatchJob, BatchOcrPage
                            session = q.get_session()
                            p_slug = info.get('project_slug')
                            if p_slug:
                                project = q.project(p_slug)
                                if project:
                                    job_type_map = {
                                        'ocr': 'UI_BATCH_OCR',
                                        'enhanced_ocr': 'UI_BATCH_ENHANCED_OCR',
                                        'translation': 'UI_BATCH_TRANSLATION',
                                    }
                                    batch_job_type = job_type_map.get(task_type)
                                    if batch_job_type:
                                        batch_item = (
                                            session.query(BatchItem)
                                            .join(BatchJob)
                                            .filter(
                                                BatchItem.project_id == project.id,
                                                BatchJob.job_type == batch_job_type,
                                            )
                                            .order_by(BatchItem.id.desc())
                                            .first()
                                        )
                                        if batch_item:
                                            total = batch_item.total_pages or len(project.pages) or 1
                                            completed = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, status='COMPLETED').count()
                                            failed = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, status='FAILED').count()
                                            
                                            if batch_item.status == 'COMPLETED' or (total > 0 and completed >= total):
                                                info['status'] = 'completed'
                                                info['progress'] = 1.0
                                                info['completed_count'] = total
                                                info['total_count'] = total
                                                updated_entries[task_id] = json.dumps(info)
                                                db_updated = True
                                            elif batch_item.status == 'FAILED':
                                                info['status'] = 'failed'
                                                info['progress'] = completed / total if total > 0 else 0
                                                info['completed_count'] = completed
                                                info['total_count'] = total
                                                updated_entries[task_id] = json.dumps(info)
                                                db_updated = True
                                            elif completed > 0 or failed > 0:
                                                info['status'] = 'running'
                                                info['progress'] = completed / total if total > 0 else 0
                                                info['completed_count'] = completed
                                                info['total_count'] = total
                                                if failed > 0:
                                                    info['failed_count'] = failed
                                                db_updated = True
                        except Exception as db_fallback_err:
                            LOG.warning(f"Error checking database fallback for task {task_id}: {db_fallback_err}")
                            
                        if not db_updated:
                            started_at_str = info.get('started_at')
                            if started_at_str:
                                try:
                                    started_at = datetime.fromisoformat(started_at_str)
                                    if (datetime.utcnow() - started_at).total_seconds() > 3600:
                                        info['status'] = 'completed'
                                        updated_entries[task_id] = json.dumps(info)
                                except Exception:
                                    pass
                else:
                    # Single result task checking (like create_project)
                    r = AsyncResult(task_id, app=celery_app)
                    state = r.state
                    
                    if state == 'SUCCESS':
                        info['status'] = 'completed'
                        info['progress'] = 1.0
                        # Try to get project slug from metadata if set
                        p_info = r.info or {}
                        if isinstance(p_info, dict) and p_info.get('slug'):
                            info['project_slug'] = p_info.get('slug')
                        updated_entries[task_id] = json.dumps(info)
                    elif state in ['FAILURE', 'REVOKED']:
                        info['status'] = 'failed'
                        info['progress'] = 0.0
                        if isinstance(r.result, Exception):
                            info['error'] = str(r.result)
                        elif isinstance(r.info, dict) and r.info.get('error'):
                            info['error'] = r.info['error']
                        updated_entries[task_id] = json.dumps(info)
                    elif state in ['PENDING', 'STARTED', 'PROGRESS', 'RETRY']:
                        info['status'] = 'running'
                        p_info = r.info or {}
                        if isinstance(p_info, dict):
                            current = p_info.get("current", 0)
                            total = p_info.get("total", 100)
                            info['progress'] = current / total if total > 0 else 0
                            info['completed_count'] = current
                            info['total_count'] = total
                            
                            if p_info.get('slug'):
                                info['project_slug'] = p_info.get('slug')
            except Exception as e:
                LOG.warning(f"Error checking Celery task status for task {task_id}: {e}")
                
        tasks.append(info)
        
    if updated_entries:
        try:
            redis_client.hset(key, mapping=updated_entries)
        except Exception as e:
            LOG.warning(f"Error caching updated task statuses in Redis: {e}")
            
    def get_started_at_key(t):
        val = t.get('started_at')
        if not val:
            return datetime.min
        try:
            # Strip trailing 'Z' if present before parsing
            if isinstance(val, str) and val.endswith('Z'):
                val = val[:-1]
            return datetime.fromisoformat(val)
        except Exception:
            return datetime.min

    tasks.sort(key=get_started_at_key, reverse=True)
    return tasks


def cancel_user_task(user_identifier, task_id):
    """
    Revokes the Celery task and updates its status in Redis to 'cancelled'.
    """
    if not user_identifier or not task_id:
        return False
        
    key = f"user_tasks:{user_identifier}"
    try:
        task_data = redis_client.hget(key, task_id)
        if not task_data:
            return False
            
        info = json.loads(safe_decode(task_data))
        status = info.get('status', 'pending')
        task_type = info.get('type')
        
        if status in ['pending', 'running']:
            # Revoke the task in Celery
            try:
                if task_type in ['ocr', 'enhanced_ocr', 'translation']:
                    r = GroupResult.restore(task_id, app=celery_app)
                    if r:
                        r.revoke(terminate=True)
                else:
                    r = AsyncResult(task_id, app=celery_app)
                    r.revoke(terminate=True)
            except Exception as e:
                LOG.warning(f"Error revoking Celery task {task_id}: {e}")
                
            # Update status to cancelled
            info['status'] = 'cancelled'
            redis_client.hset(key, task_id, json.dumps(info))
            return True
            
    except Exception as e:
        LOG.warning(f"Error cancelling user task: {e}")
        
    return False

