import pytest
from werkzeug.exceptions import HTTPException

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.utils.quotas import (
    ensure_translation_quota_for_project,
    consume_translation_credit_for_project,
)

def test_translation_quota_enforcement(client):
    session = get_session()
    
    # 1. Setup test organization, users and project
    org = db.Group(
        name="Test Org Quotas",
        slug="test-org-quotas",
        translation_credit_limit=10,
        default_user_translation_limit=5,
    )
    session.add(org)
    session.flush()

    user = db.User(
        username="quota-user",
        email="quota-user@siddhasagaram.in",
        organization_id=org.id,
    )
    session.add(user)
    session.flush()

    project = db.Project(
        slug="quota-project",
        display_title="Quota Project",
        creator_id=user.id,
    )
    # Associate project with organization
    project.groups.append(org)
    session.add(project)
    session.flush()
    session.commit()

    try:
        # Check quota when within limits (both user and org limits are 0 initially)
        ensure_translation_quota_for_project(project)  # Should not raise any error

        # 2. Test user limit enforcement
        user.translation_credits_used = 5
        session.add(user)
        session.commit()

        # Should raise 402 Payment Required for personal limit
        with pytest.raises(HTTPException) as exc_info:
            ensure_translation_quota_for_project(project)
        assert exc_info.value.code == 402
        assert "personal Translation credit limit" in exc_info.value.description

        # Reset user credits, test org limit enforcement
        user.translation_credits_used = 0
        org.translation_credits_used = 10
        session.add(user)
        session.add(org)
        session.commit()

        # Should raise 402 Payment Required for organization limit
        with pytest.raises(HTTPException) as exc_info:
            ensure_translation_quota_for_project(project)
        assert exc_info.value.code == 402
        assert "Organization/Tenant Translation credits" in exc_info.value.description

        # 3. Test consume credits
        user.translation_credits_used = 2
        org.translation_credits_used = 4
        session.add(user)
        session.add(org)
        session.commit()

        consume_translation_credit_for_project(project)

        session.refresh(user)
        session.refresh(org)
        assert user.translation_credits_used == 3
        assert org.translation_credits_used == 5

    finally:
        # Cleanup
        session.delete(project)
        session.delete(user)
        session.delete(org)
        session.commit()
