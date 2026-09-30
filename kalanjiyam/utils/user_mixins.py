from collections.abc import Iterable

from flask_login import AnonymousUserMixin, UserMixin

from kalanjiyam.enums import SiteRole


class KalanjiyamAnonymousUser(AnonymousUserMixin):
    """An anonymous user with limited permissions."""

    def has_role(self, role: SiteRole | str) -> bool:
        role_val = role.value if isinstance(role, SiteRole) else role
        return role_val == SiteRole.GUEST.value

    @property
    def is_p1(self) -> bool:
        return False

    @property
    def is_p2(self) -> bool:
        return False

    @property
    def is_proofreader(self) -> bool:
        return False

    @property
    def is_moderator(self) -> bool:
        return False

    @property
    def is_master_user(self) -> bool:
        return False

    @property
    def is_admin(self) -> bool:
        return False

    @property
    def is_super_admin(self) -> bool:
        return False

    @property
    def is_org_admin(self) -> bool:
        return False

    @property
    def is_registered_user(self) -> bool:
        return False

    @property
    def is_guest(self) -> bool:
        return True

    @property
    def is_meta_analyst(self) -> bool:
        return False

    @property
    def can_create_project(self) -> bool:
        try:
            from flask import current_app

            return bool(current_app.config.get("ENABLE_GUEST_ACCESS", True))
        except Exception:
            return True

    @property
    def is_ok(self) -> bool:
        return True


class KalanjiyamUserMixin(UserMixin):
    def has_role(self, role: SiteRole | str) -> bool:
        role_val = role.value if isinstance(role, SiteRole) else role
        return role_val in {r.name for r in self.roles}

    def has_any_role(self, *roles: Iterable[SiteRole | str]) -> bool:
        user_roles = {r.name for r in self.roles}
        flat_roles = set()
        for r in roles:
            if isinstance(r, (list, tuple, set)):
                for item in r:
                    flat_roles.add(item.value if isinstance(item, SiteRole) else item)
            else:
                flat_roles.add(r.value if isinstance(r, SiteRole) else r)
        return any(r in user_roles for r in flat_roles)

    @property
    def is_p1(self) -> bool:
        return self.has_role(SiteRole.P1) or self.has_role(SiteRole.REGISTERED_USER)

    @property
    def is_p2(self) -> bool:
        return self.has_role(SiteRole.P2)

    @property
    def is_proofreader(self) -> bool:
        return self.has_any_role(SiteRole.P1, SiteRole.P2, SiteRole.REGISTERED_USER)

    @property
    def is_moderator(self) -> bool:
        return self.has_any_role(SiteRole.MODERATOR, SiteRole.SUPER_ADMIN)

    @property
    def is_master_user(self) -> bool:
        return self.has_role(SiteRole.MASTER_USER)

    @property
    def is_admin(self) -> bool:
        return self.has_role(SiteRole.SUPER_ADMIN)

    @property
    def is_super_admin(self) -> bool:
        return self.has_role(SiteRole.SUPER_ADMIN)

    @property
    def is_org_admin(self) -> bool:
        return self.has_role(SiteRole.ORG_ADMIN) and bool(
            getattr(self, "organization_id", None)
        )

    @property
    def is_registered_user(self) -> bool:
        return self.has_role(SiteRole.REGISTERED_USER)

    @property
    def is_guest(self) -> bool:
        return False

    @property
    def is_meta_analyst(self) -> bool:
        return self.has_role(SiteRole.META_ANALYST)

    @property
    def can_create_project(self) -> bool:
        return (
            self.is_moderator
            or self.is_org_admin
            or self.is_master_user
            or self.is_super_admin
            or self.is_registered_user
        )

    @property
    def is_ok(self) -> bool:
        return not (self.is_deleted or self.is_banned)
