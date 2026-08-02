# Implementation Plan - Guest Saving and Default OCR Engine

This plan covers resolving two issues:
1. **Error 1**: Limit OCR to a single "Run OCR" button for unregistered (guest) users and registered users without an organization, running with `DEFAULT_OCR_ENGINE` loaded from `.env`.
2. **Error 2**: Permit unregistered (guest) users to save edits to pages in projects they own (fingerprint-verified).

## Proposed Changes

### Configuration

#### [MODIFY] [.env](file:///home/mrportable/Documents/kalanjiyam/.env) and [.env.example](file:///home/mrportable/Documents/kalanjiyam/.env.example)
- Add `DEFAULT_OCR_ENGINE=google` to the configuration templates.

#### [MODIFY] [config.py](file:///home/mrportable/Documents/kalanjiyam/config.py)
- Map `DEFAULT_OCR_ENGINE` to `DEFAULT_OCR_ENGINE = _env("DEFAULT_OCR_ENGINE", "google")`.

---

### Database Schema / Models

#### [MODIFY] [proofing.py (Models)](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/models/proofing.py)
- Make `author_id` in `Revision` nullable: `author_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)`.

#### [NEW] [c3d4e5f6a7b8_make_revision_author_nullable.py](file:///home/mrportable/Documents/kalanjiyam/migrations/versions/c3d4e5f6a7b8_make_revision_author_nullable.py)
- Create a new migration script using Alembic's `batch_alter_table` to alter `proof_revisions.author_id` to be nullable.

---

### Business Logic & Views

#### [MODIFY] [revisions.py](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/utils/revisions.py)
- Change `author_id` type annotation in `add_revision` to `int | None`.

#### [MODIFY] [page.py](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/views/proofing/page.py)
- Replace `@login_required` on `edit_post` view with `@p2_required` to allow guest owners of the project.
- In `edit_post`, pass `author_id=current_user.id if current_user.is_authenticated else None` to `add_revision`.
- Remove `@login_required` from `ocr` endpoint.
- Enforce guest rate limits on single-page OCR.
- Override `engine` parameter with `DEFAULT_OCR_ENGINE` and normalize languages if `is_restricted_ocr` is active.

#### [MODIFY] [project.py](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/views/proofing/project.py)
- Override `engine` parameter with `DEFAULT_OCR_ENGINE` and normalize languages in `batch_ocr` POST when `is_restricted_ocr` is active.

#### [MODIFY] [main.py](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/views/proofing/main.py)
- Filter out `None` when calculating `num_contributors`.

---

### Templates

#### [MODIFY] [edit.html](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/templates/proofing/pages/edit.html)
- Remove the old unauthenticated warning banner saying "Since you are not logged in, some functions...".
- Define `can_save = current_user.is_authenticated or (project.fingerprint_id and request.cookies.get('device_fingerprint') == project.fingerprint_id)`.
- Wrap the publish action in `can_save` check. Show the warning "Only registered users can save changes." if `can_save` is false.

#### [MODIFY] [editor-components.html](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/templates/proofing/pages/editor-components.html)
- Define `is_restricted_ocr = not current_user.is_authenticated or not current_user.organization_id`.
- If `is_restricted_ocr`, render only a single "Run OCR" button that calls `runOCR()` directly, instead of opening the dropdown panel.
- Only show "Publish" button on header if `can_save` is true.

#### [MODIFY] [batch-ocr.html](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/templates/proofing/projects/batch-ocr.html)
- Check `is_restricted_ocr`. If restricted, render a simple confirmation form with just a "Run OCR" submit button (no settings/dropdown).

#### [MODIFY] [proofing.html](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/templates/macros/proofing.html) and [revision.html](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/templates/proofing/pages/revision.html)
- Safely handle `None` values for revision author.

---

### Tests

#### [MODIFY] [test_page.py](file:///home/mrportable/Documents/kalanjiyam/test/kalanjiyam/views/proofing/test_page.py)
- Update assertions in `test_edit__unauth` to expect "Only registered users can save changes" warning and no "Publish changes".
- Add a new test case `test_edit__guest_owner` to verify that matching fingerprint cookie enables the Publish changes option.

## Verification Plan

Since testing on host is broken, the user will build and run on docker local:
- Verify that a guest/unregistered user who creates a project:
  - Can view the page editor, runs OCR (without engine choices), and saves/publishes changes successfully.
- Verify that guest users/no-org users cannot choose other OCR engines.
- Verify that guest users cannot save edits to projects they don't own.
