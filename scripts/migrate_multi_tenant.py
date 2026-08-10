#!/usr/bin/env python3
"""Multi-tenant migration safety checks and optional fixes.

Run without --apply to report issues only::

    python scripts/migrate_multi_tenant.py

Apply safe fixes (admin→super_admin, sync organization_id, dedupe user_groups)::

    python scripts/migrate_multi_tenant.py --apply

Assign ungrouped projects to a default organization::

    python scripts/migrate_multi_tenant.py --apply --default-org-slug my-org
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root so `kalanjiyam` imports work when run as `python scripts/...`.
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.orm import Session

from kalanjiyam.seed.utils.data_utils import create_db
import kalanjiyam.database as db


def _users_in_multiple_groups(session: Session) -> list[tuple[int, str, int]]:
    rows = session.execute(
        text(
            """
            SELECT u.id, u.username, COUNT(ug.group_id) AS group_count
            FROM users u
            JOIN user_groups ug ON ug.user_id = u.id
            GROUP BY u.id, u.username
            HAVING COUNT(ug.group_id) > 1
            ORDER BY group_count DESC, u.username
            """
        )
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _users_without_organization(session: Session) -> list[tuple[int, str]]:
    rows = session.execute(
        text(
            """
            SELECT u.id, u.username
            FROM users u
            WHERE u.organization_id IS NULL
              AND (u.is_deleted IS FALSE OR u.is_deleted IS NULL)
              AND (u.is_banned IS FALSE OR u.is_banned IS NULL)
              AND u.id NOT IN (
                SELECT ur.user_id
                FROM user_roles ur
                JOIN roles ro ON ro.id = ur.role_id
                WHERE ro.name = 'super_admin'
              )
            ORDER BY u.username
            """
        )
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _projects_without_org(session: Session) -> list[tuple[int, str]]:
    rows = session.execute(
        text(
            """
            SELECT p.id, p.slug
            FROM proof_projects p
            LEFT JOIN project_groups pg ON pg.project_id = p.id
            WHERE pg.project_id IS NULL
            ORDER BY p.slug
            """
        )
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _migrate_admin_to_super_admin(session: Session, apply: bool) -> int:
    admin_role = session.query(db.Role).filter_by(name="admin").first()
    super_role = session.query(db.Role).filter_by(name="super_admin").first()
    if admin_role is None or super_role is None:
        return 0

    users_with_admin = (
        session.query(db.User)
        .join(db.UserRoles, db.User.id == db.UserRoles.user_id)
        .filter(db.UserRoles.role_id == admin_role.id)
        .all()
    )
    count = 0
    for user in users_with_admin:
        count += 1
        if not apply:
            continue
        if super_role not in user.roles:
            user.roles.append(super_role)
        if admin_role in user.roles:
            user.roles.remove(admin_role)
        session.add(user)
    if apply and count:
        session.commit()
    return count


def _sync_organization_from_groups(session: Session, apply: bool) -> int:
    rows = session.execute(
        text(
            """
            SELECT user_id, MIN(group_id) AS group_id
            FROM user_groups
            GROUP BY user_id
            """
        )
    ).fetchall()
    updated = 0
    for user_id, group_id in rows:
        user = session.query(db.User).filter_by(id=user_id).first()
        if user is None:
            continue
        if user.organization_id != group_id:
            updated += 1
            if apply:
                user.organization_id = group_id
                session.add(user)
    if apply and updated:
        session.commit()
    return updated


def _dedupe_user_groups(session: Session, apply: bool) -> int:
    """Keep one user_groups row per user (lowest group_id)."""
    multi = _users_in_multiple_groups(session)
    removed = 0
    for user_id, _, _ in multi:
        memberships = (
            session.query(db.UserGroups)
            .filter_by(user_id=user_id)
            .order_by(db.UserGroups.group_id)
            .all()
        )
        keep = memberships[0]
        for row in memberships[1:]:
            removed += 1
            if apply:
                session.delete(row)
        if apply:
            user = session.query(db.User).filter_by(id=user_id).first()
            if user is not None:
                user.organization_id = keep.group_id
                session.add(user)
    if apply and removed:
        session.commit()
    return removed


def _assign_projects_to_org(session: Session, org_slug: str, apply: bool, verbose: bool = True) -> int:
    org = session.query(db.Group).filter_by(slug=org_slug).first()
    if org is None:
        raise SystemExit(f'Organization "{org_slug}" not found.')

    ungrouped = _projects_without_org(session)
    assigned = 0
    for project_id, slug in ungrouped:
        assigned += 1
        if verbose:
            print(f"  [DB LINK] {'[APPLIED]' if apply else '[DRY-RUN]'} Project '{slug}' (id={project_id}) -> Organization '{org_slug}' (id={org.id})")
        if apply:
            existing = (
                session.query(db.ProjectGroups)
                .filter_by(project_id=project_id, group_id=org.id)
                .first()
            )
            if not existing:
                session.add(db.ProjectGroups(project_id=project_id, group_id=org.id))
    if apply and assigned:
        session.commit()
        print(f"  [DB COMMIT] Committed {assigned} project organization assignments to DB.")
    return assigned


def _migrate_project_storage_folders(session: Session, apply: bool, verbose: bool = True) -> tuple[int, list[str]]:
    """Migrate historical project folders (projects/<slug> or projects/open-tenant/<slug>) to projects/<org_slug>/<slug>."""
    from kalanjiyam.utils.storage import get_storage, LocalStorage, S3Storage
    from config import create_config_only_app
    import os

    app = None
    env = os.environ.get("FLASK_ENV") or os.environ.get("APP_ENV") or "production"
    try:
        app = create_config_only_app(env)
    except Exception:
        try:
            app = create_config_only_app("default")
        except Exception:
            pass

    migrated_count = 0
    logs = []

    def _do_migration():
        nonlocal migrated_count
        try:
            storage = get_storage()
        except Exception as e:
            msg = f"Storage backend unavailable: {e}"
            logs.append(msg)
            print(f"  [STORAGE ERROR] {msg}")
            return

        projects = session.query(db.Project).all()
        print(f"\n--- Checking Storage Paths for {len(projects)} Project(s) ---")

        for project in projects:
            if not project.groups:
                if verbose:
                    print(f"  [STORAGE SKIP] Project '{project.slug}' has no organization linked yet.")
                continue
            org_slug = project.groups[0].slug
            p_slug = project.slug

            if isinstance(storage, LocalStorage):
                root = storage.root / "projects"
                legacy_dir = root / p_slug
                open_tenant_dir = root / "open-tenant" / p_slug
                target_dir = root / org_slug / p_slug

                source_dir = None
                if legacy_dir.is_dir() and legacy_dir.resolve() != target_dir.resolve():
                    source_dir = legacy_dir
                elif open_tenant_dir.is_dir() and open_tenant_dir.resolve() != target_dir.resolve() and org_slug != "open-tenant":
                    source_dir = open_tenant_dir

                if source_dir is not None:
                    rel_src = source_dir.relative_to(storage.root)
                    rel_tgt = target_dir.relative_to(storage.root)
                    file_count = sum(1 for f in source_dir.rglob("*") if f.is_file())
                    msg = f"{'[MOVED]' if apply else '[WOULD MOVE]'} {rel_src} ({file_count} file(s)) -> {rel_tgt}"
                    logs.append(msg)
                    migrated_count += 1
                    print(f"  [STORAGE MOVE] Project '{p_slug}' (Org: '{org_slug}'): {msg}", flush=True)
                    if apply:
                        try:
                            target_dir.parent.mkdir(parents=True, exist_ok=True)
                            import shutil
                            shutil.move(str(source_dir), str(target_dir))
                            print(f"    -> Successfully moved '{source_dir.name}' ({file_count} file(s)) to '{target_dir}'", flush=True)
                        except Exception as err:
                            print(f"    -> [ERROR] Move failed for '{source_dir.name}': {err}", flush=True)
                else:
                    if verbose:
                        if target_dir.is_dir():
                            print(f"  [STORAGE OK] Project '{p_slug}' already in target path 'projects/{org_slug}/{p_slug}'", flush=True)
                        else:
                            print(f"  [STORAGE NOTICE] Project '{p_slug}' (Org: '{org_slug}'): No files found on disk yet.", flush=True)

            elif isinstance(storage, S3Storage):
                legacy_prefix = f"projects/{p_slug}/"
                open_tenant_prefix = f"projects/open-tenant/{p_slug}/"
                target_prefix = f"projects/{org_slug}/{p_slug}/"

                src_prefix = None
                keys = list(storage.list_keys(legacy_prefix))
                if keys:
                    src_prefix = legacy_prefix
                elif org_slug != "open-tenant":
                    keys = list(storage.list_keys(open_tenant_prefix))
                    if keys:
                        src_prefix = open_tenant_prefix

                if src_prefix and src_prefix != target_prefix:
                    msg = f"{'[MOVED S3]' if apply else '[WOULD MOVE S3]'} {src_prefix} -> {target_prefix} ({len(keys)} objects)"
                    logs.append(msg)
                    migrated_count += 1
                    print(f"  [STORAGE MOVE S3] Project '{p_slug}' (Org: '{org_slug}'): {msg}")
                    if apply:
                        for k, _size in keys:
                            rel = k[len(src_prefix):]
                            dest_key = f"{target_prefix}{rel}"
                            try:
                                data = storage.read_bytes(k)
                                storage.save(dest_key, data)
                                storage.delete(k)
                                if verbose:
                                    print(f"    -> Copied {k} -> {dest_key}")
                            except Exception as e:
                                err_msg = f"  [ERROR] Copy {k} -> {dest_key}: {e}"
                                logs.append(err_msg)
                                print(f"    -> {err_msg}")
                else:
                    if verbose:
                        print(f"  [STORAGE OK S3] Project '{p_slug}' (Org: '{org_slug}'): S3 objects already at {target_prefix}")

    if app is not None:
        with app.app_context():
            _do_migration()
    else:
        _do_migration()

    return migrated_count, logs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply safe data fixes (otherwise report only).",
    )
    parser.add_argument(
        "--default-org-slug",
        help="When used with --apply, assign ungrouped projects to this org.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print verbose status lines for every project and file checked.",
    )
    args = parser.parse_args()

    engine = create_db()
    exit_code = 0

    print("=== Multi-Tenant Data & Storage Migration Tool ===")
    print(f"Mode: {'APPLY FIXES' if args.apply else 'DRY RUN / AUDIT ONLY'}\n")

    with Session(engine) as session:
        multi = _users_in_multiple_groups(session)
        if multi:
            exit_code = 1
            print(f"WARNING: {len(multi)} user(s) belong to multiple groups:")
            for user_id, username, count in multi[:20]:
                print(f"  - {username} (id={user_id}): {count} groups")
            if len(multi) > 20:
                print(f"  ... and {len(multi) - 20} more")

        no_org_users = _users_without_organization(session)
        if no_org_users:
            exit_code = 1
            print(f"WARNING: {len(no_org_users)} active user(s) without organization_id:")
            for user_id, username in no_org_users[:20]:
                print(f"  - {username} (id={user_id})")

        ungrouped_projects = _projects_without_org(session)
        if ungrouped_projects:
            exit_code = 1
            print(f"WARNING: {len(ungrouped_projects)} project(s) not linked to any organization:")
            for _, slug in ungrouped_projects[:20]:
                print(f"  - {slug}")

        print("\n--- Database Role & User Synchronization ---")
        admin_count = _migrate_admin_to_super_admin(session, args.apply)
        print(
            f"{'Migrated' if args.apply else 'Would migrate'} "
            f"{admin_count} admin user(s) to super_admin."
        )

        sync_count = _sync_organization_from_groups(session, args.apply)
        print(
            f"{'Synced' if args.apply else 'Would sync'} "
            f"{sync_count} user organization_id value(s) from user_groups."
        )

        dedupe_count = _dedupe_user_groups(session, args.apply)
        print(
            f"{'Removed' if args.apply else 'Would remove'} "
            f"{dedupe_count} duplicate user_groups row(s)."
        )

        if args.default_org_slug:
            print(f"\n--- Assigning Projects to Default Organization '{args.default_org_slug}' ---")
            assigned = _assign_projects_to_org(session, args.default_org_slug, args.apply, verbose=True)
            print(
                f"{'Assigned' if args.apply else 'Would assign'} "
                f"{assigned} ungrouped project(s) to org {args.default_org_slug!r}."
            )

        storage_count, storage_logs = _migrate_project_storage_folders(session, args.apply, verbose=True)
        print(f"\n=== Migration Summary ===")
        print(f"Projects assigned to org: {assigned if args.default_org_slug else 0}")
        print(f"Project storage folders moved: {storage_count}")

        if not args.apply and exit_code:
            print("\nRe-run with --apply to apply safe fixes.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
