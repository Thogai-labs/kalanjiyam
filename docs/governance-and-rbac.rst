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

* **P1 (Basic Proofer)**: Entry-level proofreader. Can mark page proofing state as reviewed-1 (Yellow / R1) and work on basic project uploads.
* **P2 (Advanced Proofer)**: Senior proofreader. Can mark page state as reviewed-2 (Green / R2), upload complex PDFs, and perform batch operations across project pages.
* **MODERATOR**: Proofing effort coordinator. Can manage project deletion, promote or restrict users within proofing scope, and run global batch operations.
* **ADMIN**: Organization administrator. Has full access to database records, project lifecycle management, and organization settings within assigned tenant scope.
* **ORG_ADMIN**: Dedicated organization manager. Manages tenant-specific users, project allocations, and organization settings.
* **SUPER_ADMIN**: Platform owner / System administrator. Has unrestricted cross-tenant access, quota control, organization lifecycle management, and system configuration capabilities.

RBAC Permissions Matrix
-----------------------

+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Capability / Feature                | P1 | P2 | MODERATOR | ADMIN | ORG_ADMIN | SUPER_ADMIN |
+=====================================+====+====+===========+=======+===========+=============+
| View Public Projects & Pages        | Yes| Yes| Yes       | Yes   | Yes       | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Edit OCR Text (R0 -> R1)            | Yes| Yes| Yes       | Yes   | Yes       | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Verify & Lock Proofing (R1 -> R2)   | No | Yes| Yes       | Yes   | Yes       | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Upload New Books / PDF Projects     | Basic|Yes| Yes      | Yes   | Yes       | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Trigger OCR Batch Re-processing     | No | Yes| Yes       | Yes   | Yes       | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Delete Projects / Pages             | No | No | Yes       | Yes   | Yes       | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Manage Organization Users           | No | No | No        | Yes   | Yes       | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Configure Tenant Quotas & Storage   | No | No | No        | No    | No        | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+
| Access System Metrics & Logs        | No | No | No        | Yes   | Yes       | Yes         |
+-------------------------------------+----+----+-----------+-------+-----------+-------------+

Tenant Governance Rules
-----------------------

1. **Multi-Tenant Data Isolation**: Users assigned to an organization can only read/modify resources (books, OCR tasks, analytics) belonging to their tenant organization unless explicitly granted system-wide `SUPER_ADMIN` privileges.
2. **Proofing Integrity Standard**: Pages marked as `R2` (reviewed-2) require validation from a user with at least `P2` role to ensure quality standards for published catalog items.
3. **Resource & Quota Limits**: OCR processing jobs and cloud storage allocations per organization are governed by tenant quotas enforced at the `SUPER_ADMIN` level.

Governance & Rule Change Log
----------------------------

This section records all modifications to access control rules, role definitions, and governance policies, along with the business or technical rationale behind each change.

+------------+----------------------+-----------------------------------+---------------------------------------------------+--------------+
| Date       | Affected Role / Rule | Description of Change             | Reason / Rationale                                | Approved By  |
+============+======================+===================================+===================================================+==============+
| 2026-07-24 | All Roles            | Initial Governance & RBAC Document| Formalize role expectations and permission matrix| Architecture |
+------------+----------------------+-----------------------------------+---------------------------------------------------+--------------+
