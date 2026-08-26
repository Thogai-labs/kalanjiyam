import logging

from sqlalchemy.orm import Session

import kalanjiyam.database as db
from kalanjiyam.enums import SiteRole
from kalanjiyam.seed.utils.data_utils import create_db


def run(engine=None):
    """Create roles and remove obsolete roles."""

    if engine is None:
        try:
            from flask import current_app
            if current_app:
                from kalanjiyam import queries as q
                engine = q.get_engine()
        except Exception:
            pass
    engine = engine or create_db()
    with Session(engine) as session:
        valid_roles = {r.value for r in SiteRole}
        roles = session.query(db.Role).all()

        for r in roles:
            if r.name not in valid_roles:
                session.query(db.UserRoles).filter_by(role_id=r.id).delete()
                session.delete(r)
                logging.debug(f"Deleted obsolete role: {r.name}")

        existing_names = {s.name for s in session.query(db.Role).all()}
        new_names = {r.value for r in SiteRole if r.value not in existing_names}

        if new_names:
            for name in new_names:
                role = db.Role(name=name)
                session.add(role)
                logging.debug(f"Created role: {name}")
        session.commit()

    logging.debug("Done. The following roles are defined:")
    with Session(engine) as session:
        for r in session.query(db.Role).all():
            logging.debug(f"- {r.name}")


if __name__ == "__main__":
    run()
