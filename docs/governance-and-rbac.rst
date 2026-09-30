Governance & RBAC Guidelines
============================

Overview
--------

Kalanjiyam enforces Role-Based Access Control (RBAC) and Multi-Tenant Isolation governance policies to safeguard digital library assets, OCR operations, and organization management.

This document details:
1. **User Roles & Hierarchy**
2. **RBAC Permissions Matrix**
3. **Tenant & Data Governance Rules**
4. **Governance Rule Change Log** (Tracking updates, rationales, and approvals)

User Roles
----------

The system defines the following roles (configured in :class:`kalanjiyam.enums.SiteRole`):

* **GUEST**: Unregistered visitor under open-tenant. Can browse public projects, proofread their own guest projects, and create projects under open-tenant when guest access is enabled.
* **REGISTERED_USER**: Self-registered individual user under open-tenant (without enterprise organization membership). Can proofread pages, edit text, and create projects within open-tenant.
* **P1 (Basic Proofer)**: Organization entry-level proofreader. Can mark page proofing state as reviewed-1 (Yellow / R1). Cannot create projects.
* **P2 (Advanced Proofer)**: Organization senior proofreader. Can mark page state as reviewed-2 (Green / R2) and perform batch operations across project pages. Cannot create projects.
* **MODERATOR**: Proofing effort coordinator. Can create and upload projects, manage project deletion, promote or restrict users within proofing scope, and run global batch operations.
* **ADMIN**: Organization administrator. Has full access to database records, project lifecycle management, and organization settings within assigned tenant scope.
* **ORG_ADMIN**: Dedicated organization manager. Manages tenant-specific users, project creation/allocations, and organization settings.
* **META_ANALYST**: Cross-organization analytics reviewer. Has read-only access exclusively to the meta-analytics dashboard (`/admin/meta-analytics/`), velocity, events, and metrics export. Cannot view or edit projects, pages, users, or platform settings, and cannot create projects.
* **SUPER_ADMIN**: Platform owner / System administrator. Has unrestricted cross-tenant access, quota control, organization lifecycle management, and system configuration capabilities.

RBAC Permissions Matrix
-----------------------

+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Capability / Feature                | GUEST | REGISTERED_USER | P1 | P2 | MODERATOR | ADMIN | ORG_ADMIN | META_ANALYST | SUPER_ADMIN |
+=====================================+=======+=================+====+====+===========+=======+===========+==============+=============+
| View Public Projects & Pages        | Yes   | Yes             | Yes| Yes| Yes       | Yes   | Yes       | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Edit OCR Text (R0 -> R1)            | Own*  | Yes             | Yes| Yes| Yes       | Yes   | Yes       | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Verify & Lock Proofing (R1 -> R2)   | No    | No              | No | Yes| Yes       | Yes   | Yes       | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Upload New Books / PDF Projects     | Yes*  | Yes             | No | No | Yes       | Yes   | Yes       | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Trigger OCR Batch Re-processing     | No    | No              | No | Yes| Yes       | Yes   | Yes       | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Delete Projects / Pages             | Own*  | No              | No | No | Yes       | Yes   | Yes       | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Manage Organization Users           | No    | No              | No | No | No        | Yes   | Yes       | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Configure Tenant Quotas & Storage   | No    | No              | No | No | No        | No    | No        | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Access System Metrics & Logs        | No    | No              | No | No | No        | Yes   | Yes       | No           | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+
| Access Meta-Analytics Dashboard     | No    | No              | No | No | No        | No    | No        | Yes          | Yes         |
+-------------------------------------+-------+-----------------+----+----+-----------+-------+-----------+--------------+-------------+

\* *For Guest users, permissions apply to their own guest-created projects (device fingerprint matched) and project creation is subject to platform guest access enablement and daily guest limits.*

Tenant Governance Rules
-----------------------

1. **Multi-Tenant Data Isolation**: Users assigned to an organization can only read/modify resources (books, OCR tasks, analytics) belonging to their tenant organization unless explicitly granted system-wide `SUPER_ADMIN` privileges.
2. **Open Tenant Scope**: Self-registered users and guest users operate under the open-tenant. They enjoy community creation and proofreading features without access to enterprise organization resources.
3. **Proofing Integrity Standard**: Pages marked as `R2` (reviewed-2) require validation from a user with at least `P2` role to ensure quality standards for published catalog items.
4. **Resource & Quota Limits**: OCR processing jobs and cloud storage allocations per organization are governed by tenant quotas enforced at the `SUPER_ADMIN` level.
5. **Source File Retention Policy**: Uploaded source `.pdf`, `.docx`, and `.doc` files are stored upon upload. When `AUTO_UPLOADED_FILES_CLEANUP` is enabled in `.env`, uploaded source document files older than 7 days are automatically purged to optimize storage, as all extracted pages and textual data are permanently persisted in the database.

Governance & Rule Change Log
----------------------------

This section records all modifications to access control rules, role definitions, and governance policies, along with the business or technical rationale behind each change.

+------------+----------------------+------------------------------------+---------------------------------------------------+--------------+
| Date       | Affected Role / Rule | Description of Change              | Reason / Rationale                                | Approved By  |
+============+======================+====================================+===================================================+==============+
| 2026-07-24 | All Roles            | Initial Governance & RBAC Document | Formalize role expectations and permission matrix | Architecture |
+------------+----------------------+------------------------------------+---------------------------------------------------+--------------+
| 2026-07-24 | Source File Storage  | Add Auto Uploaded Files Cleanup    | Delete source PDF/DOC files > 7 days as database  | Architecture |
|            |                      | policy (`AUTO_UPLOADED_FILES_CLEANUP`) | stores all page text & image records          |              |
+------------+----------------------+------------------------------------+---------------------------------------------------+--------------+
| 2026-09-29 | P1, P2, Moderator,   | Restrict project creation to       | Protect org quotas & catalog structure; P1/P2 are | Architecture |
|            | Org Admin            | Moderator, Org Admin, Super Admin  | proofreaders and reviewers, not upload managers   |              |
+------------+----------------------+------------------------------------+---------------------------------------------------+--------------+
| 2026-09-29 | Guest, Registered    | Introduce explicit Guest and       | Separate open-tenant community users from org     | Architecture |
|            | User Roles           | Registered User roles              | proofreaders; retain open-tenant project creation |              |
+------------+----------------------+------------------------------------+---------------------------------------------------+--------------+
| 2026-09-30 | Meta Analyst Role    | Introduce dedicated META_ANALYST   | Provide isolated cross-tenant analytics access    | Architecture |
|            |                      | role restricted to meta-analytics  | without granting admin, project, or proofing      |              |
|            |                      | dashboard and exports              | privileges                                        |              |
+------------+----------------------+------------------------------------+---------------------------------------------------+--------------+
