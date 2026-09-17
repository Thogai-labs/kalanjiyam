"""Cross-Organization Meta-Analytics Service.

Provides aggregated operational telemetry across tenant organizations
under a strict Zero-Content Privacy Guarantee:
- NO document content, text transcriptions, OCR bounding boxes, or revision diffs.
- NO page scans, image files, or PDF downloads.
- ONLY structural metrics, status counts, velocity indicators, AI compute,
  and system health events.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, or_

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SitePageStatus


class MetaAnalyticsService:
    """Zero-content aggregation service for cross-organization operational intelligence."""

    @staticmethod
    def get_cluster_summary() -> Dict[str, Any]:
        """Cluster-wide operational totals and high-water marks."""
        session = q.get_session()

        # 1. Organization counts
        total_orgs = session.query(db.Group).count()
        active_orgs = session.query(db.Group).filter(db.Group.is_active.is_(True)).count()

        # 2. Resource totals
        storage_row = session.query(
            func.coalesce(func.sum(db.Group.storage_used_bytes), 0),
            func.coalesce(func.sum(db.Group.storage_quota_bytes), 0),
            func.coalesce(func.sum(db.Group.ocr_credits_used), 0),
            func.coalesce(func.sum(db.Group.ocr_credit_limit), 0),
            func.coalesce(func.sum(db.Group.translation_credits_used), 0),
            func.coalesce(func.sum(db.Group.translation_credit_limit), 0),
        ).first()

        storage_used = int(storage_row[0]) if storage_row else 0
        storage_quota = int(storage_row[1]) if storage_row and storage_row[1] else None
        ocr_used = int(storage_row[2]) if storage_row else 0
        ocr_limit = int(storage_row[3]) if storage_row and storage_row[3] else None
        tr_used = int(storage_row[4]) if storage_row else 0
        tr_limit = int(storage_row[5]) if storage_row and storage_row[5] else None

        # 3. Projects and Pages counts
        total_projects = session.query(db.Project).count()
        total_pages = session.query(db.Page).count()

        # 4. Page status breakdown across the entire cluster
        status_map = {
            s.id: s.name for s in session.query(db.PageStatus).all()
        }
        status_counts_raw = (
            session.query(db.Page.status_id, func.count(db.Page.id))
            .group_by(db.Page.status_id)
            .all()
        )
        status_counts = {
            "r0": 0,
            "r1": 0,
            "r2": 0,
            "skip": 0,
            "other": 0,
        }
        for s_id, count in status_counts_raw:
            s_name = status_map.get(s_id, "")
            if s_name == SitePageStatus.R0.value:
                status_counts["r0"] += count
            elif s_name == SitePageStatus.R1.value:
                status_counts["r1"] += count
            elif s_name == SitePageStatus.R2.value:
                status_counts["r2"] += count
            elif s_name == SitePageStatus.SKIP.value:
                status_counts["skip"] += count
            else:
                status_counts["other"] += count

        # Overall completion percentage (R2 vs total non-skipped)
        reviewed_pages = status_counts["r2"]
        valid_pages = total_pages - status_counts["skip"]
        completion_pct = round((reviewed_pages / valid_pages * 100), 1) if valid_pages > 0 else 0.0

        # 5. Proofing velocity: revisions and active contributors in last 30 days
        cutoff_30d = datetime.utcnow() - timedelta(days=30)
        revisions_30d = (
            session.query(func.count(db.Revision.id))
            .filter(db.Revision.created >= cutoff_30d)
            .scalar()
            or 0
        )
        active_proofers_30d = (
            session.query(func.count(func.distinct(db.Revision.author_id)))
            .filter(
                db.Revision.created >= cutoff_30d,
                db.Revision.author_id.isnot(None),
            )
            .scalar()
            or 0
        )
        total_revisions = session.query(func.count(db.Revision.id)).scalar() or 0

        # 6. System telemetry: Errors in last 24h
        cutoff_24h = datetime.utcnow() - timedelta(days=1)
        errors_24h = (
            session.query(func.count(db.SystemMetricLog.id))
            .filter(
                db.SystemMetricLog.category == "error",
                db.SystemMetricLog.created_at >= cutoff_24h,
            )
            .scalar()
            or 0
        )

        return {
            "total_orgs": total_orgs,
            "active_orgs": active_orgs,
            "total_projects": total_projects,
            "total_pages": total_pages,
            "status_counts": status_counts,
            "completion_pct": completion_pct,
            "storage_used_bytes": storage_used,
            "storage_quota_bytes": storage_quota,
            "ocr_credits_used": ocr_used,
            "ocr_credit_limit": ocr_limit,
            "translation_credits_used": tr_used,
            "translation_credit_limit": tr_limit,
            "revisions_30d": revisions_30d,
            "active_proofers_30d": active_proofers_30d,
            "total_revisions": total_revisions,
            "errors_24h": errors_24h,
        }

    @staticmethod
    def get_org_activity_matrix(time_window_days: int = 30) -> List[Dict[str, Any]]:
        """Multi-tenant activity and health matrix for all organizations.

        Aggregates proofing volume, AI compute, and quota risk per organization.
        """
        session = q.get_session()
        cutoff = datetime.utcnow() - timedelta(days=time_window_days)
        cutoff_errors = datetime.utcnow() - timedelta(days=7)

        # Pre-fetch status map
        status_map = {s.id: s.name for s in session.query(db.PageStatus).all()}
        r0_id = next((k for k, v in status_map.items() if v == SitePageStatus.R0.value), None)
        r1_id = next((k for k, v in status_map.items() if v == SitePageStatus.R1.value), None)
        r2_id = next((k for k, v in status_map.items() if v == SitePageStatus.R2.value), None)
        skip_id = next((k for k, v in status_map.items() if v == SitePageStatus.SKIP.value), None)

        orgs = session.query(db.Group).order_by(db.Group.name).all()
        matrix = []

        for org in orgs:
            # 1. Members count
            members_count = (
                session.query(func.count(db.UserGroups.user_id))
                .filter(db.UserGroups.group_id == org.id)
                .scalar()
                or 0
            )

            # 2. Associated projects
            project_ids = [
                pg.project_id
                for pg in session.query(db.ProjectGroups.project_id)
                .filter(db.ProjectGroups.group_id == org.id)
                .all()
            ]
            projects_count = len(project_ids)

            # 3. Pages breakdown across org projects
            total_pages = 0
            r0_count = 0
            r1_count = 0
            r2_count = 0
            skip_count = 0

            if project_ids:
                page_stats = (
                    session.query(db.Page.status_id, func.count(db.Page.id))
                    .filter(db.Page.project_id.in_(project_ids))
                    .group_by(db.Page.status_id)
                    .all()
                )
                for s_id, cnt in page_stats:
                    total_pages += cnt
                    if s_id == r0_id:
                        r0_count += cnt
                    elif s_id == r1_id:
                        r1_count += cnt
                    elif s_id == r2_id:
                        r2_count += cnt
                    elif s_id == skip_id:
                        skip_count += cnt

            valid_pages = total_pages - skip_count
            completion_rate = round((r2_count / valid_pages * 100), 1) if valid_pages > 0 else 0.0

            # 4. Revisions & Active contributors in window
            revisions_in_window = 0
            active_contributors = 0
            if project_ids:
                revisions_in_window = (
                    session.query(func.count(db.Revision.id))
                    .filter(
                        db.Revision.project_id.in_(project_ids),
                        db.Revision.created >= cutoff,
                    )
                    .scalar()
                    or 0
                )
                active_contributors = (
                    session.query(func.count(func.distinct(db.Revision.author_id)))
                    .filter(
                        db.Revision.project_id.in_(project_ids),
                        db.Revision.created >= cutoff,
                        db.Revision.author_id.isnot(None),
                    )
                    .scalar()
                    or 0
                )

            # 5. Quota health calculation
            storage_used = org.storage_used_bytes or 0
            storage_quota = org.storage_quota_bytes
            storage_pct = 0.0
            if storage_quota and storage_quota > 0:
                storage_pct = round(min(100.0, (storage_used / storage_quota) * 100), 1)

            ocr_used = org.ocr_credits_used or 0
            ocr_limit = org.ocr_credit_limit
            ocr_pct = 0.0
            if ocr_limit and ocr_limit > 0:
                ocr_pct = round(min(100.0, (ocr_used / ocr_limit) * 100), 1)

            tr_used = org.translation_credits_used or 0
            tr_limit = org.translation_credit_limit
            tr_pct = 0.0
            if tr_limit and tr_limit > 0:
                tr_pct = round(min(100.0, (tr_used / tr_limit) * 100), 1)

            # 6. System errors for this org in last 7 days
            org_errors_7d = (
                session.query(func.count(db.SystemMetricLog.id))
                .filter(
                    db.SystemMetricLog.group_id == org.id,
                    db.SystemMetricLog.category == "error",
                    db.SystemMetricLog.created_at >= cutoff_errors,
                )
                .scalar()
                or 0
            )

            # 7. Overall health assessment badge
            if not org.is_active:
                health = "INACTIVE"
            elif storage_pct >= 90.0 or ocr_pct >= 90.0 or tr_pct >= 90.0 or org_errors_7d > 20:
                health = "CRITICAL"
            elif storage_pct >= 75.0 or ocr_pct >= 75.0 or tr_pct >= 75.0 or org_errors_7d > 5:
                health = "WARNING"
            else:
                health = "HEALTHY"

            matrix.append({
                "org_id": org.id,
                "name": org.name,
                "slug": org.slug,
                "is_active": org.is_active,
                "created_at": org.created_at.isoformat() if org.created_at else None,
                "members_count": members_count,
                "active_contributors": active_contributors,
                "projects_count": projects_count,
                "total_pages": total_pages,
                "r0_count": r0_count,
                "r1_count": r1_count,
                "r2_count": r2_count,
                "skip_count": skip_count,
                "completion_rate": completion_rate,
                "revisions_in_window": revisions_in_window,
                "storage_used_bytes": storage_used,
                "storage_quota_bytes": storage_quota,
                "storage_pct": storage_pct,
                "ocr_credits_used": ocr_used,
                "ocr_credit_limit": ocr_limit,
                "ocr_pct": ocr_pct,
                "translation_credits_used": tr_used,
                "translation_credit_limit": tr_limit,
                "tr_pct": tr_pct,
                "errors_count": org_errors_7d,
                "health": health,
            })

        # Sort matrix: active orgs first, then by revisions_in_window desc
        matrix.sort(key=lambda x: (x["is_active"], x["revisions_in_window"], x["total_pages"]), reverse=True)
        return matrix

    @staticmethod
    def get_workflow_velocity_trends(days: int = 30, org_id: Optional[int] = None) -> Dict[str, Any]:
        """Returns daily aggregated time-series of revisions, OCR jobs, and system errors."""
        session = q.get_session()
        today = datetime.utcnow().date()
        date_list = [today - timedelta(days=i) for i in reversed(range(days))]
        date_strs = [d.isoformat() for d in date_list]

        start_dt = datetime.combine(date_list[0], datetime.min.time())

        # 1. Query revisions per day
        rev_query = session.query(
            func.date(db.Revision.created).label("rev_date"),
            func.count(db.Revision.id).label("count"),
        ).filter(db.Revision.created >= start_dt)

        if org_id:
            project_ids = [
                pg.project_id
                for pg in session.query(db.ProjectGroups.project_id)
                .filter(db.ProjectGroups.group_id == org_id)
                .all()
            ]
            if project_ids:
                rev_query = rev_query.filter(db.Revision.project_id.in_(project_ids))
            else:
                rev_query = rev_query.filter(db.Revision.id == -1)

        rev_by_date = {row[0]: row[1] for row in rev_query.group_by("rev_date").all()}

        # 2. Query errors per day
        err_query = session.query(
            func.date(db.SystemMetricLog.created_at).label("err_date"),
            func.count(db.SystemMetricLog.id).label("count"),
        ).filter(
            db.SystemMetricLog.category == "error",
            db.SystemMetricLog.created_at >= start_dt,
        )
        if org_id:
            err_query = err_query.filter(db.SystemMetricLog.group_id == org_id)

        err_by_date = {row[0]: row[1] for row in err_query.group_by("err_date").all()}

        # 3. Format aligned lists
        revisions_series = []
        errors_series = []
        cumulative_rev = 0
        cumulative_series = []

        for d in date_list:
            d_key = d.isoformat()
            rev_cnt = rev_by_date.get(d_key, 0)
            if not rev_cnt and d in rev_by_date:
                rev_cnt = rev_by_date[d]

            err_cnt = err_by_date.get(d_key, 0)
            if not err_cnt and d in err_by_date:
                err_cnt = err_by_date[d]

            revisions_series.append(rev_cnt)
            errors_series.append(err_cnt)
            cumulative_rev += rev_cnt
            cumulative_series.append(cumulative_rev)

        return {
            "dates": date_strs,
            "revisions": revisions_series,
            "errors": errors_series,
            "cumulative_revisions": cumulative_series,
            "total_revisions_in_period": sum(revisions_series),
            "total_errors_in_period": sum(errors_series),
        }

    @staticmethod
    def get_meta_events_stream(
        limit: int = 40,
        org_id: Optional[int] = None,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Synthesize high-level operational events across orgs with zero document content."""
        session = q.get_session()
        events: List[Dict[str, Any]] = []

        # 1. Batch jobs completed / failed
        batch_query = session.query(db.BatchJob).order_by(desc(db.BatchJob.created_at)).limit(limit)
        batch_jobs = batch_query.all()

        for bj in batch_jobs:
            if event_type and event_type != "BATCH":
                break

            # Find org associated with this batch job if available
            org_name = "Platform"
            bj_org_id = None
            if bj.items:
                first_proj_id = bj.items[0].project_id
                if first_proj_id:
                    pg = session.query(db.ProjectGroups).filter_by(project_id=first_proj_id).first()
                    if pg:
                        bj_org_id = pg.group_id
                        org_obj = session.query(db.Group).filter_by(id=pg.group_id).first()
                        if org_obj:
                            org_name = org_obj.name

            if org_id is not None and bj_org_id != org_id:
                continue

            total_pages = sum(item.total_pages or 0 for item in bj.items)
            is_success = bj.status == "COMPLETED"
            severity = "success" if is_success else ("error" if bj.status == "FAILED" else "info")

            job_title = f"{bj.job_type.replace('_', ' ').title()} Job #{bj.id}"
            job_desc = f"Status: {bj.status}. Processed {len(bj.items)} items ({total_pages} total pages)."
            if bj.error_message and not is_success:
                job_desc += f" Reason: {bj.error_message[:120]}"

            events.append({
                "id": f"bj-{bj.id}",
                "timestamp": bj.completed_at.isoformat() if bj.completed_at else bj.created_at.isoformat(),
                "org_id": bj_org_id,
                "org_name": org_name,
                "event_type": "BATCH",
                "severity": severity,
                "title": job_title,
                "description": job_desc,
            })

        # 2. System error logs
        err_query = (
            session.query(db.SystemMetricLog)
            .filter(db.SystemMetricLog.category == "error")
        )
        if org_id:
            err_query = err_query.filter(db.SystemMetricLog.group_id == org_id)

        err_records = err_query.order_by(desc(db.SystemMetricLog.created_at)).limit(limit).all()

        for err in err_records:
            if event_type and event_type != "ERROR":
                break

            org_name = err.group.name if err.group else "Platform"
            err_level = err.error_level or "ERROR"
            severity = "error" if err_level in ("CRITICAL", "ERROR") else "warning"

            events.append({
                "id": f"err-{err.id}",
                "timestamp": err.created_at.isoformat() if err.created_at else datetime.utcnow().isoformat(),
                "org_id": err.group_id,
                "org_name": org_name,
                "event_type": "ERROR",
                "severity": severity,
                "title": f"System Alert: {err.name}",
                "description": f"Level: {err_level}. {err.error_message or 'Internal runtime fault recorded.'}",
            })

        # 3. Quota threshold alerts
        if not event_type or event_type == "QUOTA":
            org_pool = (
                session.query(db.Group)
                .filter(db.Group.is_active.is_(True))
            )
            if org_id:
                org_pool = org_pool.filter(db.Group.id == org_id)

            for g in org_pool.all():
                # Check storage quota
                if g.storage_quota_bytes and g.storage_quota_bytes > 0:
                    pct = (g.storage_used_bytes or 0) / g.storage_quota_bytes
                    if pct >= 0.85:
                        used_mb = (g.storage_used_bytes or 0) // (1024 * 1024)
                        quota_mb = g.storage_quota_bytes // (1024 * 1024)
                        events.append({
                            "id": f"quota-storage-{g.id}",
                            "timestamp": (g.updated_at or g.created_at).isoformat(),
                            "org_id": g.id,
                            "org_name": g.name,
                            "event_type": "QUOTA",
                            "severity": "warning" if pct < 0.95 else "error",
                            "title": f"High Storage Utilization ({round(pct * 100)}%)",
                            "description": f"Tenant has consumed {used_mb} MB of its {quota_mb} MB disk allocation.",
                        })

                # Check OCR credits limit
                if g.ocr_credit_limit and g.ocr_credit_limit > 0:
                    ocr_pct = (g.ocr_credits_used or 0) / g.ocr_credit_limit
                    if ocr_pct >= 0.85:
                        events.append({
                            "id": f"quota-ocr-{g.id}",
                            "timestamp": (g.updated_at or g.created_at).isoformat(),
                            "org_id": g.id,
                            "org_name": g.name,
                            "event_type": "QUOTA",
                            "severity": "warning" if ocr_pct < 0.95 else "error",
                            "title": f"OCR Credit Exhaustion ({round(ocr_pct * 100)}%)",
                            "description": f"Tenant consumed {g.ocr_credits_used} of {g.ocr_credit_limit} allocated OCR credits.",
                        })

        # Sort all combined events by timestamp descending
        events.sort(key=lambda e: e["timestamp"], reverse=True)
        return events[:limit]

    @staticmethod
    def get_org_meta_profile(org_id: int) -> Optional[Dict[str, Any]]:
        """Isolated metadata profile for a single tenant organization (strictly zero content)."""
        session = q.get_session()
        org = session.query(db.Group).filter_by(id=org_id).first()
        if not org:
            return None

        # 1. Members
        members = (
            session.query(db.User)
            .join(db.UserGroups, db.UserGroups.user_id == db.User.id)
            .filter(db.UserGroups.group_id == org.id)
            .order_by(db.User.username)
            .all()
        )
        members_count = len(members)

        # 2. Associated projects
        project_ids = [
            pg.project_id
            for pg in session.query(db.ProjectGroups.project_id)
            .filter(db.ProjectGroups.group_id == org.id)
            .all()
        ]

        projects = (
            session.query(db.Project)
            .filter(db.Project.id.in_(project_ids))
            .order_by(desc(db.Project.created_at))
            .all()
            if project_ids
            else []
        )

        # Pre-fetch status map
        status_map = {s.id: s.name for s in session.query(db.PageStatus).all()}
        r0_id = next((k for k, v in status_map.items() if v == SitePageStatus.R0.value), None)
        r1_id = next((k for k, v in status_map.items() if v == SitePageStatus.R1.value), None)
        r2_id = next((k for k, v in status_map.items() if v == SitePageStatus.R2.value), None)
        skip_id = next((k for k, v in status_map.items() if v == SitePageStatus.SKIP.value), None)

        # Build project metadata rows (strictly metadata: title, slug, genre, status breakdown)
        project_meta_list = []
        org_total_pages = 0
        org_r0 = 0
        org_r1 = 0
        org_r2 = 0
        org_skip = 0

        for p in projects:
            p_pages_count = len(p.pages)
            p_r0 = sum(1 for pg in p.pages if pg.status_id == r0_id)
            p_r1 = sum(1 for pg in p.pages if pg.status_id == r1_id)
            p_r2 = sum(1 for pg in p.pages if pg.status_id == r2_id)
            p_skip = sum(1 for pg in p.pages if pg.status_id == skip_id)

            p_valid = p_pages_count - p_skip
            p_comp = round((p_r2 / p_valid * 100), 1) if p_valid > 0 else 0.0

            org_total_pages += p_pages_count
            org_r0 += p_r0
            org_r1 += p_r1
            org_r2 += p_r2
            org_skip += p_skip

            project_meta_list.append({
                "id": p.id,
                "display_title": p.display_title,
                "slug": p.slug,
                "genre_name": p.genre.name if p.genre else "Unclassified",
                "total_pages": p_pages_count,
                "r0_count": p_r0,
                "r1_count": p_r1,
                "r2_count": p_r2,
                "skip_count": p_skip,
                "completion_pct": p_comp,
                "created_at": p.created_at.strftime("%b %d, %Y") if p.created_at else "Unknown",
            })

        org_valid = org_total_pages - org_skip
        overall_completion = round((org_r2 / org_valid * 100), 1) if org_valid > 0 else 0.0

        # 3. Quota calculations
        storage_used = org.storage_used_bytes or 0
        storage_quota = org.storage_quota_bytes
        storage_pct = round(min(100.0, (storage_used / storage_quota) * 100), 1) if storage_quota else 0.0

        ocr_used = org.ocr_credits_used or 0
        ocr_limit = org.ocr_credit_limit
        ocr_pct = round(min(100.0, (ocr_used / ocr_limit) * 100), 1) if ocr_limit else 0.0

        tr_used = org.translation_credits_used or 0
        tr_limit = org.translation_credit_limit
        tr_pct = round(min(100.0, (tr_used / tr_limit) * 100), 1) if tr_limit else 0.0

        # 4. Recent revisions count
        cutoff_30d = datetime.utcnow() - timedelta(days=30)
        revisions_30d = 0
        active_contributors_30d = 0
        if project_ids:
            revisions_30d = (
                session.query(func.count(db.Revision.id))
                .filter(
                    db.Revision.project_id.in_(project_ids),
                    db.Revision.created >= cutoff_30d,
                )
                .scalar()
                or 0
            )
            active_contributors_30d = (
                session.query(func.count(func.distinct(db.Revision.author_id)))
                .filter(
                    db.Revision.project_id.in_(project_ids),
                    db.Revision.created >= cutoff_30d,
                    db.Revision.author_id.isnot(None),
                )
                .scalar()
                or 0
            )

        # 5. Member velocity breakdown (metadata only: username, email domain, role names, revision volume)
        member_stats = []
        user_ids = [m.id for m in members]
        if user_ids:
            rev_counts = dict(
                session.query(db.Revision.author_id, func.count(db.Revision.id))
                .filter(db.Revision.author_id.in_(user_ids))
                .group_by(db.Revision.author_id)
                .all()
            )
            for m in members:
                member_stats.append({
                    "id": m.id,
                    "username": m.username,
                    "roles": [r.name for r in m.roles],
                    "revisions_count": rev_counts.get(m.id, 0),
                    "created_at": m.created_at.strftime("%b %d, %Y") if m.created_at else None,
                })
            member_stats.sort(key=lambda x: x["revisions_count"], reverse=True)

        return {
            "org": {
                "id": org.id,
                "name": org.name,
                "slug": org.slug,
                "description": org.description,
                "is_active": org.is_active,
                "created_at": org.created_at.strftime("%b %d, %Y") if org.created_at else "Unknown",
                "admin_username": org.admin_user.username if org.admin_user else "(None)",
            },
            "summary": {
                "members_count": members_count,
                "projects_count": len(projects),
                "total_pages": org_total_pages,
                "r0_count": org_r0,
                "r1_count": org_r1,
                "r2_count": org_r2,
                "skip_count": org_skip,
                "completion_pct": overall_completion,
                "revisions_30d": revisions_30d,
                "active_contributors_30d": active_contributors_30d,
                "storage_used_bytes": storage_used,
                "storage_quota_bytes": storage_quota,
                "storage_pct": storage_pct,
                "ocr_credits_used": ocr_used,
                "ocr_credit_limit": ocr_limit,
                "ocr_pct": ocr_pct,
                "translation_credits_used": tr_used,
                "translation_credit_limit": tr_limit,
                "tr_pct": tr_pct,
            },
            "projects": project_meta_list,
            "members": member_stats,
            "events": MetaAnalyticsService.get_meta_events_stream(limit=25, org_id=org.id),
        }

    @staticmethod
    def export_meta_metrics_csv(time_window_days: int = 30) -> str:
        """Export the cross-organization matrix as a CSV formatted string (strictly metadata)."""
        matrix = MetaAnalyticsService.get_org_activity_matrix(time_window_days)
        out = io.StringIO()
        writer = csv.writer(out)

        # Header
        writer.writerow([
            "Organization ID",
            "Organization Name",
            "Slug",
            "Status",
            "Members Count",
            f"Active Proofers ({time_window_days}d)",
            "Projects Count",
            "Total Pages",
            "R0 Needs Work",
            "R1 Proofread 1x",
            "R2 Approved",
            "Skipped",
            "Completion Rate (%)",
            f"Revisions ({time_window_days}d)",
            "Storage Used (MB)",
            "Storage Quota (MB)",
            "Storage Quota (%)",
            "OCR Credits Used",
            "OCR Credit Limit",
            "Translation Credits Used",
            "Translation Credit Limit",
            "Errors (7d)",
            "Health Indicator",
        ])

        for row in matrix:
            storage_mb = row["storage_used_bytes"] // (1024 * 1024)
            quota_mb = (row["storage_quota_bytes"] // (1024 * 1024)) if row["storage_quota_bytes"] else "Unlimited"
            ocr_limit = row["ocr_credit_limit"] if row["ocr_credit_limit"] is not None else "Unlimited"
            tr_limit = row["translation_credit_limit"] if row["translation_credit_limit"] is not None else "Unlimited"

            writer.writerow([
                row["org_id"],
                row["name"],
                row["slug"],
                "Active" if row["is_active"] else "Inactive",
                row["members_count"],
                row["active_contributors"],
                row["projects_count"],
                row["total_pages"],
                row["r0_count"],
                row["r1_count"],
                row["r2_count"],
                row["skip_count"],
                row["completion_rate"],
                row["revisions_in_window"],
                storage_mb,
                quota_mb,
                row["storage_pct"],
                row["ocr_credits_used"],
                ocr_limit,
                row["translation_credits_used"],
                tr_limit,
                row["errors_count"],
                row["health"],
            ])

        return out.getvalue()
