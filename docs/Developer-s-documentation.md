# Documentation Draft for Kalanjiyam

## Installation (Local Dev with Docker)

### Steps for Ubuntu / Linux
1. Make sure you have installed Docker on your system.
2. Give permissions to your current user to run Docker by running the following:
   ```bash
   sudo usermod -aG docker $USER
   newgrp docker
   ```
3. Clone/download the repository, copy `.env.example` to `.env`, and update the configuration values in the `.env` file as needed:
   ```bash
   cp .env.example .env
   ```

   #### Environment Configuration (`.env`) Details:
   Behind the scenes, Kalanjiyam is configured using environment variables defined in the `.env` file. Below are the key configuration groups you should review:

   ##### A. Connection Strings
   * **`SQLALCHEMY_DATABASE_URI`**: The connection URI for your database.
     * For **Docker development**, this is set to: `postgresql://kalanjiyam:kalanjiyam@kalanjiyam-db/kalanjiyam` (connecting to the Postgres container).
     * For **non-Docker local development**, this defaults to SQLite: `sqlite:///database.db`.
   * **`REDIS_URL`**: The connection URI for the Redis broker used by Celery tasks.
     * For **Docker development**, this is set to: `redis://kalanjiyam-redis:6379/0`.
     * For **non-Docker local development**, it points to localhost: `redis://localhost:6379/0`.

   ##### B. Multi-Tenant / Organization Settings
   * **`MULTI_TENANT_MODE`** (`true` or `false`): Enable/disable organization-scoped tenancy controls.
     * If `true`, users and books are separated into distinct organizations. Recommended `true` for staging/production environments.
   * **`ENFORCE_ORG_ACCESS`** (`true` or `false`): When `MULTI_TENANT_MODE` is enabled, this restricts project views and API access to members of the owning organization.
   * **`DEFAULT_PROJECT_REQUIRES_ORG`** (`true` or `false`): Set to `true` to require any new project upload to be associated with an organization. Set to `false` if you want users to upload projects outside organizations.

   ##### C. File Uploads
   * **`FLASK_UPLOAD_FOLDER`**: The directory where uploaded PDFs and processed book page images will be stored.
     * > [!IMPORTANT]
     * > This **must** be an absolute path (e.g. `/tmp` or `/home/user/uploads`). If a relative path is used, the application validation will fail on startup.
     * In production/staging Docker environments, this is mapped to `/data/uploads` inside the container, which points to a persistent directory on the host.
   * **`KALANJIYAM_DATA_DIR`**: The host directory where all application uploads/data will be stored.
     * If empty or not defined, this defaults to `~/kalanjiyam-data`.
     * **On servers with restricted home directory quotas**, point this to a spacious mount point (e.g., `KALANJIYAM_DATA_DIR=/home1/student/username/kalanjiyam-data`).

4. Make sure in the `Makefile` (located at the root of the project) that `KALANJIYAM_DEPLOYMENT_ENV` is set to `local`.
5. Start the Docker services by running the following command:
   ```bash
   make docker-start
   ```
6. When the process finishes starting the services, you will see a success message:
   ```bash
   Container kalanjiyam-redis-dev  Started
   Container kalanjiyam-celery-dev Started
   Container kalanjiyam-web-dev    Started
   Container kalanjiyam-db-dev     Started
   Kalanjiyam WebApp   : ✔ 
   Kalanjiyam URL      : http://0.0.0.0:5002
   
   To stop, run "make docker-stop".
   ```
7. Visit the site at [http://localhost:5002](http://localhost:5002).

## Managing Users & Organizations via the CLI

All user and organization management is performed using the CLI tool. Inside the running web container (e.g. `kalanjiyam-web-dev` for local dev, `kalanjiyam-web-staging` for staging, or `kalanjiyam-web-prod` for production), the tool is located at `scripts/cli.py` (which corresponds to `cli.py` at the root of the host workspace). Run these commands as follows:

### 1. Create a Super Admin
If you need platform-wide administrator privileges to manage settings, organizations, and all other accounts, run:
```bash
docker exec -it kalanjiyam-web-dev python scripts/cli.py create-super-admin
```
*(Note: Only one super admin account is allowed on the platform).*

### 2. Create a New Organization (Tenant)
```bash
docker exec -it kalanjiyam-web-dev python scripts/cli.py create-organization --name "My Org" --slug "my-org"
```

### 3. Create a Regular User inside an Organization
To create a new user account linked to a specific organization (defaults to the `p1` basic proofer role):
```bash
docker exec -it kalanjiyam-web-dev python scripts/cli.py create-org-user --org "my-org" --username "test_user" --email "user@example.com"
```

### 4. Assign / Update an Organization Admin (Org Admin)
To assign or promote a user to be the administrator of an organization:
```bash
docker exec -it kalanjiyam-web python scripts/cli.py assign-org-admin --org "org-slug" --username "username"
```

### 5. Update / Add a Role to a User
To grant other roles (like `p2` or `moderator`) to an existing user:
```bash
docker exec -it kalanjiyam-web python scripts/cli.py add-role --username "username" --role "role-name"
```
*(Available roles: `p1`, `p2`, `moderator`, `org_admin`)*

### 6. Change a User's Password
To change the password for any user account via the command line:
```bash
docker exec -it kalanjiyam-web python scripts/cli.py change-password --username "username"
```

---

## Roles and Permissions Reference Table

Here is the complete permission reference table for all user roles in Kalanjiyam (defined in `SiteRole` in `kalanjiyam/enums.py`):

| Role | Scope | Key Permissions & Capabilities | Limitations & Access Scope |
| :--- | :--- | :--- | :--- |
| **`super_admin`** | Platform-Wide | <ul><li>Create/edit all organizations and manage storage and OCR credit quotas.</li><li>Global User Management (create/edit users, assign roles).</li><li>Access global Flask-Admin CRUD (`/admin/`) for users, projects, and dictionaries.</li><li>Access global metrics dashboard (`/admin/platform/`).</li><li>Import and export books globally.</li></ul> | **Platform Owner**: Only **one** super admin account is allowed. Can only be created/modified via the CLI. |
| **`admin`** | Platform-Wide | <ul><li>Legacy platform administrator. Access to administrative dashboards, metrics, and data operations.</li><li>Used as check alias in code decorators (`is_admin`).</li></ul> | **Legacy**: For fine-grained multi-tenancy and organization administration, migrate to `super_admin` or `org_admin`. |
| **`org_admin`** | Organization-Scoped | <ul><li>Manage organization members and assign roles within their organization (`/admin/org/`).</li><li>Create projects and upload PDFs for their organization.</li><li>Toggle projects as "Public" to make them viewable on `/books/`.</li><li>Export projects owned by their organization.</li></ul> | **Tenant Admin**: Scoped strictly to their assigned organization. Cannot access platform-wide views (`/admin/platform/`) or other organizations' quotas. |
| **`moderator`** | Platform-Wide | <ul><li>Run batch operations across the entire proofing effort.</li><li>Delete projects, promote users, or ban users globally.</li><li>Access the admin metrics dashboard (`/proofing/admin/dashboard/`).</li></ul> | Cannot edit organizations, quotas, or access Flask-Admin CRUD. |
| **`p2`** | Project / Org Scoped | <ul><li>Proofread and mark pages as **reviewed-2 (Green)** (Finalized/Approved).</li><li>Upload PDF documents to create new projects.</li><li>Run operations across an entire project.</li></ul> | Cannot manage users, delete projects, or manage quotas. |
| **`p1`** | Project / Org Scoped | <ul><li>Proofread and mark pages as **reviewed-1 (Yellow)** (Proofread once).</li><li>Upload simple project documents (if permitted by the organization).</li></ul> | Cannot mark pages as reviewed-2 (Green) or run project-wide admin operations. |

---

## Seeding the Database

### 1. Automatic Seeding (Out of the Box)
When you run `make docker-start` for the first time (if the database file does not exist), the setup process automatically runs migrations and seeds the following core data:
* **Lookup roles and page statuses** (`kalanjiyam.seed.lookup`)
* **GRETIL Sanskrit texts** (`kalanjiyam.seed.texts.gretil`)
* **DCS (Digital Corpus of Sanskrit) parse data** (`kalanjiyam.seed.dcs`)

At this point, a basic database is ready for local development.

### 2. Manual Seeding (Optional)
If you want to seed additional dictionaries or texts, you can run the seed scripts directly inside the running web container:

* **Seed Dictionaries**:
  * **Monier-Williams Dictionary**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.dictionaries.monier
    ```
  * **Apte Sanskrit-English Dictionary**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.dictionaries.apte
    ```
  * **Apte Sanskrit-Hindi Dictionary**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.dictionaries.apte_sanskrit_hindi
    ```
  * **Amarakosha**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.dictionaries.amarakosha
    ```
  * **Shabdakalpadruma**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.dictionaries.shabdakalpadruma
    ```
  * **Shabdartha Kaustubha**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.dictionaries.shabdartha_kaustubha
    ```
  * **Shabdasagara**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.dictionaries.shabdasagara
    ```
  * **Vacaspatyam**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.dictionaries.vacaspatyam
    ```
* **Seed Texts**:
  * **Ramayana Text**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.texts.ramayana
    ```
  * **Mahabharata Text**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.texts.mahabharata
    ```
  * **GRETIL Texts**:
    ```bash
    docker exec -it kalanjiyam-web python -m kalanjiyam.seed.texts.gretil
    ```
*(Note: Seeding larger datasets can take several minutes as they fetch data over the network).*

---

## Managing the Database & Migrations

For database schema versioning and updates, Kalanjiyam uses **Alembic**. This ensures database schema changes are safely tracked and applied.

When running with Docker, all database migration commands should be executed inside the running `kalanjiyam-web` container:

### 1. Check current migration version
To check the current active database schema revision:
```bash
docker exec -it kalanjiyam-web alembic current
```

### 2. Apply pending migrations
To apply all pending database migrations and bring the schema up to the latest version:
```bash
docker exec -it kalanjiyam-web alembic upgrade head
```

### 3. Create a new migration (For Contributors)
If you modify the database models (in the `models/` directory) and need to generate a new schema migration:
```bash
docker exec -it kalanjiyam-web alembic revision --autogenerate -m "description of changes"
```
*(Note: Generating a new migration file inside the container will automatically save it to your host's `./migrations/versions/` directory thanks to Docker volume mapping).*

---

## Useful Development Commands

When developing, you run tests and linters locally on your host machine inside the activated Python virtual environment:

### 1. Setup Local Environment
Ensure you have created and activated the local Python virtual environment:
```bash
# Create venv and install dependencies
make install-python

# Activate virtual environment
source env/bin/activate
```

### 2. Run Python Unit Tests
To run all Python tests using `pytest` on the host:
```bash
make test
```

To run the unit tests inside the active running Docker container:
```bash
docker exec -it kalanjiyam-web pytest
```

To run a specific test file inside the container (e.g., versioning tests):
```bash
docker exec -it kalanjiyam-web pytest test/kalanjiyam/views/proofing/test_proofing_versions.py
```

### 3. Run Python Linters and Formatters
To run code formatting checks (`black` and `ruff`) on the host:
```bash
make py-lint
```

### 4. Run Frontend Linters and Tests
To check and test JavaScript assets:
```bash
# Lint JavaScript
make js-lint

# Run Jest unit tests
make js-test
```

### 5. Access the PostgreSQL Database Console
To launch a PostgreSQL shell (`psql`) directly inside the database container to inspect tables or run SQL commands:
```bash
docker exec -it kalanjiyam-db psql -U kalanjiyam -d kalanjiyam
```
*(Default password is `kalanjiyam`. Type `\dt` to list all tables, and `\q` to exit).*

### 6. Monitor Celery Background Task Logs
To view celery worker task queues and debug background jobs (like OCR or PDF processing):
```bash
docker logs -f kalanjiyam-celery
```

---

## Codebase Directory Guide

To help you navigate the codebase, here is a brief overview of the key directories and files in Kalanjiyam:

### 1. Core Application (`/kalanjiyam`)
This directory contains the main Flask application code:
* **`/models`**: Database model definitions using SQLAlchemy (e.g., `auth.py` for user credentials, `proofing.py` for OCR page/revision tracking, and `texts.py` for library books).
* **`/views`**: Flask blueprints containing the routing logic, HTTP request handlers, and HTML page controllers (e.g., `/views/proofing/` for proofreading pages, `/views/auth.py` for user authentication).
* **`/tasks`**: Celery background tasks and asynchronous job definitions (e.g., `projects.py` for processing uploaded PDFs and executing OCR).
* **`/seed`**: Database initialization and seed scripts, partitioned into `lookup` data (default roles/statuses), `dictionaries` (lexicons like Monier-Williams/Apte), and `texts` (Sanskrit corpora).
* **`/templates`**: Jinja2 HTML templates defining the server-rendered web interface.
* **`/static`**: Frontend assets compiled using Tailwind CSS and esbuild, including CSS stylesheets, JavaScript files, fonts, and images.
* **`/utils`**: Helper utility modules (e.g., `storage.py` for POSIX/S3-compatible file storage, `auth.py` for session decorations).

### 2. Infrastructure & Tooling (Root)
* **`/deploy`**: Docker Compose environment configurations (logical groups for `local/` dev, `staging/`, and `prod/` release environments).
* **`/migrations`**: Database schema migration script version history generated and tracked by Alembic.
* **`/tests` or `/test`**: Pytest and Jest test suites for backend and frontend validation.
* **`cli.py`**: Command-line administrative utility script.
* **`config.py`**: Reads configuration environment variables and sets up application configuration groups.
* **`Makefile`**: Standard build task automations for compiling assets, running linters, launching Docker environments, and running tests.

---

## Database Schema Overview

Kalanjiyam's data models are managed dynamically using SQLAlchemy. Below is the complete entity-relationship diagram of all 41 tables defined across `/models/`:

```mermaid
erDiagram
    %% Auth System
    users {
        int id PK
        string username
        string password_hash
        string email
        datetime created_at
        string description
        boolean is_deleted
        boolean is_banned
        boolean is_verified
        int organization_id FK "groups.id"
        int ocr_credits_used
        int translation_credits_used
    }
    roles {
        int id PK
        string name
        datetime created_at
    }
    user_roles {
        int user_id PK "FK users.id"
        int role_id PK "FK roles.id"
    }
    auth_password_reset_tokens {
        int id PK
        int user_id FK "users.id"
        string token_hash
        boolean is_active
        datetime created_at
        datetime used_at
    }

    %% Multi-tenancy Groups
    groups {
        int id PK
        string name
        string slug
        string description
        boolean is_active
        bigint storage_quota_bytes
        bigint storage_used_bytes
        int ocr_credit_limit
        int ocr_credits_used
        int translation_credit_limit
        int translation_credits_used
        bigint default_user_storage_limit
        int default_user_ocr_limit
        int default_user_translation_limit
        int admin_user_id FK "users.id"
        datetime created_at
        datetime updated_at
    }
    user_groups {
        int user_id PK "FK users.id"
        int group_id PK "FK groups.id"
    }

    %% Discussion Forums (Talk)
    discussion_boards {
        int id PK
        string title
    }
    discussion_threads {
        int id PK
        string title
        int board_id FK "discussion_boards.id"
        int author_id FK "users.id"
        datetime created_at
        datetime updated_at
    }
    discussion_posts {
        int id PK
        int board_id FK "discussion_boards.id"
        int thread_id FK "discussion_threads.id"
        int author_id FK "users.id"
        datetime created_at
        datetime updated_at
        text content
    }

    %% Proofing System
    genres {
        int id PK
        string name
    }
    proof_projects {
        int id PK
        string slug
        string source_book_id
        string display_title
        string print_title
        string author
        string editor
        string publisher
        string publication_year
        string worldcat_link
        string subtitle
        string place_of_publication
        string edition
        string series
        string subject
        text description
        text notes
        text page_numbers
        json extracted_metadata
        datetime created_at
        datetime updated_at
        int board_id FK "discussion_boards.id"
        int creator_id FK "users.id"
        int genre_id FK "genres.id"
        boolean is_publicly_viewable
        string fingerprint_id
    }
    project_groups {
        int group_id PK "FK groups.id"
        int project_id PK "FK proof_projects.id"
    }
    proof_pages {
        int id PK
        int project_id FK "proof_projects.id"
        string slug
        int order
        int version
        text ocr_bounding_boxes
        int page_width
        int page_height
        int status_id FK "proof_page_statuses.id"
    }
    proof_page_statuses {
        int id PK
        string name
    }
    proof_page_versions {
        int id PK
        int page_id FK "proof_pages.id"
        string version_key
        int version
        datetime updated_at
    }
    proof_revisions {
        int id PK
        int project_id FK "proof_projects.id"
        int page_id FK "proof_pages.id"
        int author_id FK "users.id"
        int status_id FK "proof_page_statuses.id"
        int page_version_id FK "proof_page_versions.id"
        datetime created
        string summary
        text content
        json document
        string content_format
    }
    proof_translations {
        int id PK
        int page_id FK "proof_pages.id"
        int revision_id FK "proof_revisions.id"
        int author_id FK "users.id"
        text content
        string source_language
        string target_language
        string translation_engine
        string status
        datetime created_at
        datetime updated_at
    }
    proof_ocr_comparisons {
        int id PK
        int project_id FK "proof_projects.id"
        string engine
        string status
        datetime created_at
        json summary_metrics
        json page_results
        text error_message
    }

    %% Batch Ingestion System
    batch_jobs {
        int id PK
        string target_uri
        string status
        boolean extract_metadata
        text error_message
        datetime created_at
        datetime completed_at
    }
    batch_items {
        int id PK
        int job_id FK "batch_jobs.id"
        int project_id FK "proof_projects.id"
        string file_path
        string mime_type
        string engine
        string status
        float avg_confidence
        int total_chars
        datetime created_at
        datetime completed_at
    }
    batch_ocr_chunks {
        int id PK
        int batch_item_id FK "batch_items.id"
        int chunk_index
        string status
        datetime created_at
        datetime completed_at
    }
    batch_ocr_pages {
        int id PK
        int chunk_id FK "batch_ocr_chunks.id"
        int batch_item_id FK "batch_items.id"
        int page_id FK "proof_pages.id"
        int page_number
        string status
        float confidence
        datetime created_at
        datetime completed_at
    }

    %% Archival Metadata Extraction
    metadata_extraction_runs {
        int id PK
        int project_id FK "proof_projects.id"
        string status
        string engine
        string model_name
        string taxonomy_version
        int windows_total
        int windows_completed
        int pages_read
        int fields_filled
        float avg_field_confidence
        float evidence_verified_rate
        int total_prompt_tokens
        int total_completion_tokens
        datetime created_at
        datetime completed_at
    }
    metadata_windows {
        int id PK
        int run_id FK "metadata_extraction_runs.id"
        int window_index
        string status
        json page_slugs
        string text_hash
        int fields_returned
        float engine_latency_ms
        datetime created_at
        datetime completed_at
    }
    metadata_fields {
        int id PK
        int run_id FK "metadata_extraction_runs.id"
        int project_id FK "proof_projects.id"
        string tag_code
        json value
        json curated_value
        boolean is_curated
        float confidence
        string source
        datetime created_at
    }
    metadata_evidence {
        int id PK
        int field_id FK "metadata_fields.id"
        int value_index
        string page_slug
        string block_id
        text quote
        boolean verified
        datetime created_at
    }

    %% Search Indexing
    search_index_jobs {
        int id PK
        string job_type
        string scope_kind
        int scope_org_id FK "groups.id"
        int scope_project_id FK "proof_projects.id"
        string status
        int total_docs
        int processed_docs
        datetime started_at
        datetime completed_at
    }

    %% Library Texts System
    texts {
        int id PK
        string slug
        string title
        text header
    }
    text_groups {
        int group_id PK "FK groups.id"
        int text_id PK "FK texts.id"
    }
    text_sections {
        int id PK
        int text_id FK "texts.id"
        string slug
        string title
    }
    text_blocks {
        int id PK
        int text_id FK "texts.id"
        int section_id FK "text_sections.id"
        string slug
        text xml
        int n
    }
    block_parses {
        int id PK
        int text_id FK "texts.id"
        int block_id FK "text_blocks.id"
        text data
    }

    %% Dictionaries
    dictionaries {
        int id PK
        string slug
        string title
    }
    dictionary_entries {
        int id PK
        int dictionary_id FK "dictionaries.id"
        string key
        string value
    }

    %% Blog & Site Telemetry
    blog_posts {
        int id PK
        int author_id FK "users.id"
        datetime created_at
        datetime updated_at
        string title
        string slug
        text content
    }
    site_project_sponsorship {
        int id PK
        string sa_title
        string en_title
        text description
        int cost_inr
    }
    contributor_info {
        int id PK
        string name
        string title
        text description
    }
    system_settings {
        int id PK
        string key
        string value
        string description
        datetime updated_at
    }
    system_metric_logs {
        int id PK
        string metric_name
        float metric_value
        string context_data
        datetime recorded_at
    }
    usage_logs {
        int id PK
        int user_id FK "users.id"
        string action
        string resource_type
        int resource_id
        datetime created_at
    }
    reported_issues {
        int id PK
        int user_id FK "users.id"
        int project_id FK "proof_projects.id"
        int page_id FK "proof_pages.id"
        text issue_description
        string status
        datetime created_at
    }

    %% Relationships
    users ||--o{ user_roles : "has roles"
    roles ||--o{ user_roles : "assigned to"
    users ||--o{ auth_password_reset_tokens : "requests"
    groups ||--o{ users : "has primary members"
    users ||--o{ user_groups : "member of"
    groups ||--o{ user_groups : "contains"
    groups ||--o{ text_groups : "has access to texts"
    texts ||--o{ text_groups : "accessible by groups"
    groups ||--o{ project_groups : "owns proof projects"
    proof_projects ||--o{ project_groups : "owned by groups"

    discussion_boards ||--o{ discussion_threads : "contains"
    users ||--o{ discussion_threads : "creates"
    discussion_boards ||--o{ discussion_posts : "contains"
    discussion_threads ||--o{ discussion_posts : "contains"
    users ||--o{ discussion_posts : "writes"

    users ||--o{ proof_projects : "creates"
    discussion_boards ||--o{ proof_projects : "associated with"
    genres ||--o{ proof_projects : "categorizes"
    proof_projects ||--o{ proof_pages : "contains"
    proof_page_statuses ||--o{ proof_pages : "defines status of"
    proof_projects ||--o{ proof_revisions : "tracks revisions for"
    proof_pages ||--o{ proof_revisions : "has history of"
    users ||--o{ proof_revisions : "submits"
    proof_page_statuses ||--o{ proof_revisions : "status during revision"
    proof_pages ||--o{ proof_page_versions : "has parallel tracks"
    proof_page_versions ||--o{ proof_revisions : "contains revisions"
    proof_pages ||--o{ proof_translations : "translated from"
    proof_revisions ||--o{ proof_translations : "revision translated"
    users ||--o{ proof_translations : "translates"
    proof_projects ||--o{ proof_ocr_comparisons : "compared in"

    batch_jobs ||--o{ batch_items : "contains items"
    proof_projects ||--o{ batch_items : "created by"
    batch_items ||--o{ batch_ocr_chunks : "divided into"
    batch_ocr_chunks ||--o{ batch_ocr_pages : "tracks pages in"
    proof_pages ||--o{ batch_ocr_pages : "mapped to"

    proof_projects ||--o{ metadata_extraction_runs : "described by"
    metadata_extraction_runs ||--o{ metadata_windows : "executed in windows"
    metadata_extraction_runs ||--o{ metadata_fields : "generates tags"
    proof_projects ||--o{ metadata_fields : "curated tags for"
    metadata_fields ||--o{ metadata_evidence : "substantiated by"

    groups ||--o{ search_index_jobs : "scopes search rebuild"
    proof_projects ||--o{ search_index_jobs : "scopes project reindex"

    texts ||--o{ text_sections : "has divisions"
    texts ||--o{ text_blocks : "contains blocks"
    text_sections ||--o{ text_blocks : "contains blocks in section"
    texts ||--o{ block_parses : "has parse data for"
    text_blocks ||--o{ block_parses : "parsed in"

    dictionaries ||--o{ dictionary_entries : "contains entries"
    users ||--o{ blog_posts : "authors"
    users ||--o{ usage_logs : "triggers actions"
    users ||--o{ reported_issues : "submits reports"
```

*(Note: These correspond directly to the database tables mapped by SQLAlchemy models in the [kalanjiyam/models/](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/models/) directory).*

### Table Summary:

1. **`users`**: Platform user accounts storing credentials, status flags (verified, deleted, banned), organization associations, and quota credit usages.
2. **`roles`**: Permissions scopes (e.g. `p1`, `p2`, `moderator`, `org_admin`, `super_admin`).
3. **`user_roles`**: Many-to-many lookup connecting users with their system roles.
4. **`auth_password_reset_tokens`**: Stores hashed tokens for security recovery operations.
5. **`groups`**: Tenants/organizations managing resource quotas (storage, OCR, translation), per-user limits, and administrative mappings.
6. **`user_groups`**: Many-to-many membership linking users to their secondary groups.
7. **`texts`**: Digital library base documents featuring meta-header definitions (TEI).
8. **`text_sections`**: Hierarchical subdivisions of library texts (e.g. chapters, cantos).
9. **`text_blocks`**: Reusable text fragments (e.g. verses, paragraphs) containing raw XML.
10. **`text_groups`**: Association table mapping text access permissions to tenant groups.
11. **`block_parses`**: Lexical/grammatical analysis strings associated with library text blocks.
12. **`genres`**: Categories (e.g. Kavya, Shastra) to classify proofreading projects.
13. **`proof_projects`**: Tracking elements representing books in the proofreading queue, including bibliographic metadata and public visibility flags.
14. **`project_groups`**: Association table mapping projects to their owning organizations.
15. **`proof_pages`**: Individual page records containing OCR bounding boxes, native dimensions, and status.
16. **`proof_page_statuses`**: Enumerated validation status types (`reviewed-0`, `reviewed-1`, etc.).
17. **`proof_page_versions`**: Parallel branch/version tracks for page edits (such as user-specific and OCR engine tracks) performing optimistic locking.
18. **`proof_revisions`**: Transcription edit history recording plain-text and structured documents, linked to a specific version track.
19. **`proof_translations`**: Keeps translations of revisions across languages and engines (e.g., GPT, Google, IndicTrans2).
20. **`proof_ocr_comparisons`**: Analytics for comparing OCR engine results against manual proofing ground truth.
21. **`batch_jobs`**: High-level execution records for bulk folder/S3 ingestions.
22. **`batch_items`**: Document-level progress and metrics within a batch job.
23. **`batch_ocr_chunks`**: Celery worker chunk subdivisions for parallelized batch OCR.
24. **`batch_ocr_pages`**: Per-page OCR tracking, latency, and confidence scores for batch items.
25. **`metadata_extraction_runs`**: Archival description extraction passes holding whole-document rollups and token accounting.
26. **`metadata_windows`**: Single model extraction calls across budgeted token windows.
27. **`metadata_fields`**: Individual archival description tag entries (both machine-generated and human-curated).
28. **`metadata_evidence`**: Verbatim citation spans linking metadata facts to page image bounding boxes.
29. **`search_index_jobs`**: Asynchronous tracking records for OpenSearch rebuilds, reconciliations, and syncing.
30. **`discussion_boards`**: Associated forum board instances.
31. **`discussion_threads`**: Forum topic structures created by users.
32. **`discussion_posts`**: Thread comments/posts compiled under a forum thread.
33. **`blog_posts`**: Announcements and updates authored by system operators.
34. **`site_project_sponsorship`**: Public donation goals to support book digitizations.
35. **`contributor_info`**: Public recognition list for contributors and moderators.
36. **`system_settings`**: Key-value runtime platform configuration store.
37. **`system_metric_logs`**: System latency and resource telemetry logs.
38. **`usage_logs`**: User activity and audit trail logs.
39. **`reported_issues`**: User-submitted problem reports on projects or pages.
40. **`dictionaries`**: Lexicon definitions mapping to various languages.
41. **`dictionary_entries`**: Lexical index mappings containing value definitions in XML.
42. **`alembic_version`**: Schema migration states tracked internally by Alembic.

---

## Project Storage Architecture & Scalability (20M+ Pages)

Kalanjiyam uses a **hybrid dual-tier storage strategy** designed to scale cost-effectively and performantly to **20 million+ pages** and beyond.

```
                               ┌─────────────────────────────────────────────────────────┐
                               │                     Kalanjiyam Core                     │
                               └────────────────────────────┬────────────────────────────┘
                                                            │
                            ┌───────────────────────────────┴───────────────────────────────┐
                            ▼                                                               ▼
        ┌───────────────────────────────────────┐                       ┌───────────────────────────────────────┐
        │        PostgreSQL Relational DB       │                       │        S3 / Object Storage (POSIX)    │
        ├───────────────────────────────────────┤                       ├───────────────────────────────────────┤
        │ • `Revision.content` (Plain Text)     │                       │ • Page Scans (`.jpg`)                 │
        │ • Book/Author/Project Metadata        │                       │ • `Revision.document` (`.json.gz`)   │
        │ • User Accounts & Role Permissions    │                       │ • Visual Cropped Elements (Tables)    │
        │ • Full-Text Search (FTS) Indexes      │                       │ • Cached Renderings & Exports         │
        └───────────────────────────────────────┘                       └───────────────────────────────────────┘
```

### 1. Storage Footprint Comparison (at 20 Million Pages)

| Layer | Data Content | Storage Location | Size Per Page | 20 Million Pages Total |
| :--- | :--- | :--- | :--- | :--- |
| **Searchable Plain Text** | Raw text string (`Revision.content`) | **PostgreSQL** | ~2 KB | **~40 GB** |
| **Bounding Box JSON (Uncompressed)** | Full spatial tokens, lines, bboxes | *PostgreSQL (Anti-pattern)* | ~75 KB | **~1.5 TB (Massive DB Bloat)** ❌ |
| **Bounding Box JSON (Gzipped)** | Spatial tokens, lines, bboxes (`.json.gz`) | **S3 / Object Storage** | ~6 KB | **~120 GB in Object Storage** ✅ |
| **Page Scans** | Standardized 200-DPI JPEG page images | **S3 / Object Storage** | ~150 KB | **~3.0 TB in Object Storage** ✅ |

---

### 2. Why Store `Revision.content` (Plain Text) in PostgreSQL?

* **Sub-Millisecond Full-Text Search**: PostgreSQL GIN and trigram indexes can search across millions of pages in milliseconds without needing to decompress or parse JSON files from S3.
* **Instant Script & Language Profiling**: Profiling script distribution (e.g. 90% Tamil, 10% English) and token counts streams directly from PostgreSQL in milliseconds.
* **Fast Version Diffing**: Proofreading comparisons between revisions (e.g. OCR text vs. human-edited text) execute fast line-by-line diffs on `content`.
* **Zero Buffer Pool Thrashing**: Because rows are only ~2 KB each, the entire 40 GB database table fits on inexpensive NVMe SSDs and stays cached in server RAM.

---

### 3. Why Offload `Revision.document` (`.json.gz`) to S3 / Object Storage?

* **Eliminates Database TOAST Bloat**: Storing 1.5 TB of JSON inside PostgreSQL causes extreme TOAST table bloat, slow `VACUUM` maintenance, and heavy backup/replication costs.
* **90%+ Compression Ratio**: Gzipping spatial JSON reduces per-page storage from ~75 KB to ~6 KB, keeping 20 million pages at just ~120 GB in cheap object storage (~$2.50/month).
* **On-Demand Lazy Decompression**: The heavy bounding box JSON is only fetched and decompressed (`load_revision_document()`) when a user opens that specific page in the interactive Replica/Flow editor or during archival metadata extraction.

---

### 4. Zero-Memory Streaming Pipeline (`O(1)` RAM Usage)

When batch OCR jobs, exports, or metadata extraction pipelines process large books (e.g. a 2,000-page manuscript):
* **Batched DB Streaming**: Pages stream out of the database in batches of 250 (`_STREAM_BATCH = 250`).
* **Flat Memory Footprint**: Python process memory consumption remains bounded at **under 50 MB RAM** regardless of whether the archive contains 100 pages or 20,000,000 pages.

---

### 5. Canonical Storage Key Layout (S3 / VersityGW)

Files are organized in S3 using a multi-tenant prefix hierarchy:

```
projects/
└── {org_slug}/
    └── {project_slug}/
        ├── pages/
        │   ├── 1.jpg
        │   ├── 2.jpg
        │   └── ...
        └── revisions/
            └── {page_slug}/
                ├── ocr-{engine}.json.gz          # e.g. ocr-surya.json.gz, ocr-google.json.gz
                ├── user-{username}.json.gz       # e.g. user-admin01.json.gz
                └── translation-{engine}.json.gz   # e.g. translation-gemini-en.json.gz
```

---

## Archival Metadata Extraction Pipeline (`/v1/metadata`)

Kalanjiyam includes an automated archival metadata extraction pipeline that reads every page of a project in token-budgeted windows, asks the extraction microservice (`POST /v1/metadata`) to describe each window, verifies citation evidence, and reduces window outputs into unified archival metadata records (ISAD(G), ISAAR(CPF), and RiC-CM elements).

```
Kalanjiyam Server                                  Metadata Microservice
       │                                                    │
       │ ─── POST /v1/metadata (Window 3 of 24) ──────────> │ (Server-side schema-guided
       │     Payload: Typed blocks, tag list,               │  decoding & prompt execution)
       │              taxonomy version                      │
       │ <─── 200 OK (Single JSON Payload) ──────────────── │
       │      Includes: Fields, per-value evidence citations,│
       │      token usage, engine latency                   │
       ▼                                                    │
Kalanjiyam verifies quotes, stores window metrics,
and reduces all windows into one description per project.
```

### 1. How Pages are Divided into Windows

The windowing algorithm (`kalanjiyam.utils.project_metadata.iter_windows` and `plan_windows`) divides documents into windows using token budgets and script awareness:

* **Script Token Profiling**: Token density is estimated based on the dominant writing script:
  * **Latin script (`Latn`)**: ~3.0 chars/token.
  * **Indic / Non-Latin scripts (`_default`)**: ~1.2 chars/token (pessimistic ratio accounting for conjuncts/matras).
  * A safety multiplier of `TOKEN_SAFETY_FACTOR = 0.85` is applied.
* **Token Budget (`WINDOW_TOKEN_BUDGET = 20_000`)**: Each window targets 20,000 tokens of input text, reserving space in the 32k context window for system taxonomy instructions (~3k tokens) and generation completion (~4.5k tokens).
  $$\text{budget\_chars} = \lfloor \text{WINDOW\_TOKEN\_BUDGET} \times (\text{chars\_per\_token} \times 0.85) \rfloor$$
* **Whole Pages Preserved**: Pages are never split mid-page across windows. If a single page exceeds the budget, it gets a window to itself.
* **1-Page Overlap (`WINDOW_OVERLAP_PAGES = 1`)**: The last page of each window is carried forward into the beginning of the next window. This guarantees that facts, multi-page sentences, signatures, and dates spanning page seams appear in both windows and are captured.
* **Lazy Streaming & Content Hashing**: Pages stream in DB batches of 250 (`_STREAM_BATCH = 250`) to keep worker RAM usage constant (<50 MB). A SHA-256 hash (`window_hash`) over block text enables skipping unchanged windows during incremental re-runs (`STATUS_SKIPPED`).

---

### 2. What is Sent to the Backend Endpoint (`POST /v1/metadata`)

Kalanjiyam dispatches window extraction requests to the metadata service with automatic failover between primary (`OCR_SERVICE_URL`) and fallback (`OCR_SERVICE_URL_2`) endpoints.

* **Endpoint**: `POST {OCR_SERVICE_URL}/v1/metadata`
* **Headers**: `Content-Type: application/json`, `X-API-Key: <OCR_SERVICE_API_KEY>`

#### Request Payload Structure:

```json
{
  "contract_version": "1.0",
  "unit_id": "kalanjiyam:project/kalat-1932-17",
  "window": {
    "index": 3,
    "total": 24,
    "page_slugs": ["61", "62", "63"]
  },
  "taxonomy_version": "client-2026-08",
  "tags": [
    "TITLE",
    "DATE",
    "CREATOR",
    "SCOPE CONTENT",
    "PERSON NAME",
    "PLACE",
    "ORGANISATION",
    "SUBJECT"
  ],
  "language_hint": ["fa", "ur", "en"],
  "pages": [
    {
      "page_slug": "61",
      "ocr_confidence": 0.94,
      "blocks": [
        {
          "id": "b1",
          "type": "heading",
          "reading_order": 1,
          "text": "Grant of an honorary commission"
        },
        {
          "id": "b2",
          "type": "paragraph",
          "reading_order": 2,
          "text": "First line of body text..."
        }
      ]
    }
  ]
}
```

| Field | Type | Description |
| :--- | :--- | :--- |
| `contract_version` | String | Must be `"1.0"`. |
| `unit_id` | String | Unique document identifier (e.g. `kalanjiyam:project/x`). |
| `window` | Object | Current window `index`, `total` planned windows, and `page_slugs`. |
| `taxonomy_version` | String | Taxonomy schema version (e.g. `"client-2026-08"`). |
| `tags` | Array[String] | Authoritative whitelist of requested tags. Write-locked tags are automatically filtered out. |
| `language_hint` | Array[String] | Known language codes (e.g. `["fa", "ur", "en"]`). |
| `pages` | Array[Object] | Ordered list of pages in the window, each containing `page_slug`, nullable `ocr_confidence`, and structured `blocks`. |
| ↳ `blocks` | Array[Object] | Typed layout blocks containing `id`, `type`, `reading_order`, and `text`. |

---

### 3. Prompt Architecture & Schema Handling

* **No Natural Language Prompt in the Wire Payload**: The HTTP request contains only structured page blocks and taxonomy tags. The system prompt, extraction instructions, few-shot examples, and grammar constraints live on the microservice.
* **Write-Locked Tags Excluded**: Custodial history and access restriction tags are write-locked (human archivist curated). They are excluded from `tags` before sending and stripped if returned by the model.
* **Internal Reference Prompt**: The function `build_prompt()` in `kalanjiyam.utils.archival_taxonomy` defines the canonical prompt instruction for local evaluation, test suites, and offline auditing.

---

### 4. Evidence Verification & Reduction

* **Quote Verification**: Every `record` field is checked verbatim against the original block text sent in the request. If the quote is missing or hallucinated, its confidence score is set to `0.0`.
* **Coordinate Linking**: The `block_id` links verified quotes directly to spatial bounding boxes in `Revision.document`, allowing readers to click any fact in the catalogue and highlight its bounding box on the original page image.
* **Reduction (`reduce_windows`)**: Once all windows complete, window fields are merged into a canonical project description. Single-value fields pick the highest confidence verified value; entity lists are deduplicated and merged.
* **Bibliographic Write-Down**: Search-facing columns in `Project` (title, author, publication year, etc.) are seeded from the verified archival fields without overwriting user-curated data.

---

### 5. Extraction Metrics & Window Calculations

Both per-window and whole-run performance metrics are tracked in the database models (`MetadataExtractionRun`, `MetadataWindow`) and rendered on the **Extraction Metrics** dashboard (`/admin/platform/metadata_metrics`) and via the CLI (`python cli.py metadata-runs`):

#### A. Window Calculation Algorithm
1. **Track Resolution (`resolve_extraction_tracks`)**: Identifies the highest-tier transcription track for each page (`moderator` > `role:p2` > `role:p1` > `ocr:<engine>`). Pages `< 50` characters are treated as blank and skipped.
2. **Script Token Profiling**: Converts token budget into character limits based on detected scripts:
   $$\text{chars\_per\_token} = \begin{cases} 3.0 & \text{Latin (Latn)} \\ 1.2 & \text{Indic / Non-Latin (Devanagari, Tamil, etc.)} \end{cases}$$
   $$\text{budget\_chars} = \lfloor 20000 \times (\text{chars\_per\_token} \times 0.85) \rfloor$$
3. **Greedy Partitioning (`plan_windows`)**: Pages are packed in reading order until `used_chars + page.char_len > budget_chars`. Pages are never split mid-page.
4. **Boundary Overlap (`WINDOW_OVERLAP_PAGES = 1`)**: Carries the last page of Window $N$ into Window $N+1$ so cross-page dates, entity names, and signatures are not missed.
5. **Incremental SHA-256 Hashing (`window_hash`)**: Hashes page slugs, block IDs, and texts. Re-runs skip unchanged windows (`STATUS_SKIPPED`), consuming zero model tokens.

#### B. Telemetry & Performance Formulae
* **Tokens Per Window:**
  $$\text{tokens\_per\_window} = \frac{\text{total\_prompt\_tokens} + \text{total\_completion\_tokens}}{\text{windows\_completed}}$$
* **Average Wall-Clock Duration Per Window:**
  $$\text{avg\_time\_per\_window\_sec} = \frac{\text{completed\_at} - \text{created\_at} \text{ (in seconds)}}{\text{windows\_completed}}$$
* **Average Engine Latency Per Window:**
  $$\text{avg\_engine\_latency\_per\_window} = \frac{\text{total\_engine\_latency\_ms}}{1000 \times \text{windows\_completed}}$$
* **Evidence Verification Rate:**
  $$\text{evidence\_verified\_rate} = \frac{\text{verified\_evidence\_citations}}{\text{total\_evidence\_citations}}$$
* **Field Fill Rate:**
  $$\text{fields\_fill\_rate} = \frac{\text{fields\_filled}}{\text{fields\_total}} \quad (\text{out of 22 standard tags})$$

---

## Production Deployment (with Docker)

For production deployments (e.g., staging or live production servers like `siddhasagaram.in`), you should use the dedicated deployment script **`./deploy/prod/deploy.sh`** rather than `make docker-start`. 

The script runs automated checks (validating environment keys, ports, and configuration states) and applies database schema migrations prior to launching the server.

### Step 1: Configure `.env` for Production
Ensure the following variables are defined in your `.env` file at the root of the workspace:

```env
# General App Configuration
FLASK_ENV=production
APPLICATION_URL_PREFIX=/kalanjiyam
SECRET_KEY=your_very_strong_random_secret_key
KALANJIYAM_BOT_PASSWORD=your_strong_bot_password

# Database Settings
POSTGRES_PASSWORD=your_strong_db_password
SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:your_strong_db_password@kalanjiyam-db/kalanjiyam

# File Storage & Uploads Folder (Mapped inside the container to /data/uploads)
FLASK_UPLOAD_FOLDER=/srv/kalanjiyam/uploads
# Host directory where all application uploads/data will be stored.
# If empty or not defined, defaults to ~/kalanjiyam-data.
# On servers with restricted home directory quotas, point this to a spacious mount point
# (e.g., KALANJIYAM_DATA_DIR=/home1/student/username/kalanjiyam-data).
KALANJIYAM_DATA_DIR=/home1/student/username/kalanjiyam-data

# Network Bindings
KALANJIYAM_HOST_IP=127.0.0.1
KALANJIYAM_HOST_PORT=5000
```

### Step 2: Configure S3 Storage (Recommended for Prod)
To use the S3-compatible backend (facilitated by the bundled `versitygw` Posix adapter):
```env
STORAGE_BACKEND=s3
S3_BUCKET=uploads
S3_ACCESS_KEY_ID=your_s3_access_key
S3_SECRET_ACCESS_KEY=your_s3_secret_access_key
```
*(If you want to keep the local filesystem backend instead, set `STORAGE_BACKEND=local`).*

### Step 3: Run the Production Deployment Command
Run the script from the root directory of the workspace:
```bash
./deploy/prod/deploy.sh
```
*(This will run validation checks, compile the production Docker image, run migrations, and launch all services in detached mode).*

### Useful Production Management Commands
* **Check Logs:**
  ```bash
  ./deploy/prod/deploy.sh logs
  ```
* **Stop Services:**
  ```bash
  ./deploy/prod/deploy.sh stop
  ```
* **Restart Services:**
  ```bash
  ./deploy/prod/deploy.sh restart
  ```
* **Run Database Migrations Only:**
  ```bash
  ./deploy/prod/deploy.sh migrate
  ```

---

## Production Deployment Checklist

To transition from local development to a live production environment, follow this checklist to ensure stability, performance, and security:

### 1. Deploy the Standalone OCR Service First
* The OCR service is a resource-intensive standalone service. It must be deployed and running **before** the main Flask application starts processing projects.
* Ensure it is running on port `5002` (or your configured port).
* Verify that the main Flask application's `.env` configuration has the correct `OCR_SERVICE_URL` and `OCR_SERVICE_API_KEY`.

### 2. Use PostgreSQL instead of SQLite
* **SQLite is for local development only**; it lacks the locking and concurrency support required for multiple simultaneous users.
* For staging/production, configure a PostgreSQL database (e.g. Postgres 15).
* Ensure that `MULTI_TENANT_MODE=true` and `ENFORCE_ORG_ACCESS=true` are configured in your production `.env` to partition data.

### 3. Run the Flask App behind Gunicorn (WSGI)
* Do **not** run the Flask built-in development server in production.
* Run the app using Gunicorn (as defined in `scripts/start_server_prod.sh`). The Docker production image does this by default with Gunicorn workers.

### 4. Configure Nginx as a Reverse Proxy with TLS/HTTPS
* Set up Nginx on the host machine to proxy traffic to the Gunicorn server (typically listening on port `5000` inside the container).
* Configure SSL/TLS certificates (e.g. using Let's Encrypt / Certbot) on Nginx to enforce secure `HTTPS` connections.
* Configure proxy limits in your Nginx config:
  * Increase `client_max_body_size` (e.g. to `50M` or more) to allow users to upload large book PDFs.
  * Increase proxy timeouts (`proxy_read_timeout` to `300`) to handle long-running HTTP uploads and OCR requests.

---

## Service Configurations (reCAPTCHA and Sentry)

These services are optional for local development but required for certain features.

### 1. Google reCAPTCHA v2 Setup (Anti-spam)
To enable reCAPTCHA v2 for user registration and password resets:
1. Go to the [Google reCAPTCHA Console](https://www.google.com/recaptcha/admin) and create a **reCAPTCHA v2 ("I'm not a robot" checkbox)** key pair.
2. Add your keys to the `.env` file:
   ```env
   RECAPTCHA_PUBLIC_KEY=your_site_key
   RECAPTCHA_PRIVATE_KEY=your_secret_key
   ```
*(Note: Unlike what is written in the main installation docs, reCAPTCHA v2 uses site/secret keys, not a JSON credentials file).*

### 2. Sentry Setup (Error Logging)
To log server exceptions in production:
1. Create a project on [Sentry.io](https://sentry.io/).
2. Copy your **DSN** (Data Source Name).
3. Add the DSN to the `.env` file:
   ```env
   SENTRY_DSN=https://your_key@sentry.io/your_project_id
   ```

---

## OCR Integration & Editing Mechanics

Kalanjiyam delegates optical character recognition to an external OCR microservice architecture:
- **Primary Endpoint**: Configured via `OCR_SERVICE_URL` and `OCR_SERVICE_API_KEY`.
- **Fallback Endpoint**: Configured via `OCR_SERVICE_URL_2` and `OCR_SERVICE_API_KEY_2`. If the primary endpoint is unreachable or returns a 5xx error, the client automatically failovers to the fallback target.
- **Engine Resolution**: Engine selection supports both canonical unmasked identifiers (`surya`, `google`, `tesseract`, `deepseek`, `glm_ocr`, `dots_ocr`) and numeric masked keys (`"1"`, `"3"`, `"5"`). The system normalizes input and sends the unmasked technical service name to the microservice.

### 1. Frontend Editing Modes

When proofreaders open a page, the editing interface supports two primary views to compare the recognized texts against the original page scan:

* **Replica Mode (Default):**
  * **Layout:** Displays the original page scan image with interactive OCR bounding box overlays on the left pane, and a spatial-scaled page replica on the right pane.
  * **Interactivity:** Proofreaders can click directly on any text block on the page to edit the text in place within the spatial layer.
  * **Benefits:** Retains columns, headers, tables, and exact page layout structure, making it easy to identify where text is missing or misaligned relative to the scan.
* **Flow Mode:**
  * **Layout:** A continuous rich-text editor (using TipTap) on one side, paired with a standard image viewer pane on the other.
  * **Interactivity:** Standard text-editing workflow for writing and editing text flow.
  * **Syncing:** Running OCR in Replica mode automatically parses, structure-clusters, and syncs the recognized text layout to Flow mode.
  * **Workflow:** Revise and proofread layout blocks in Replica, and use Flow mode for formatting adjustments or continuous plain text editing.

---

### 2. OCR Service Response Contract (v2.2)

To ensure loose coupling, Kalanjiyam communicates with the external OCR service via a strict engine-agnostic API contract. The external OCR service MUST return a JSON payload with a `Content-Type: application/json` header. 

If the service returns a payload matching this shape, it will automatically plug into the frontend editor without code changes.

#### A. JSON Schema Definition
The JSON payload must include the following top-level and block-level properties:

```json
{
  "contract_version": "2.2",
  "engine": "surya",
  "model": {
    "name": "surya-rec",
    "version": "0.6.1"
  },
  "source_type": "scan",
  "coordinate_space": "pixel",
  "page_width": 1240,
  "page_height": 1754,
  "page_confidence": 0.942,
  "page_p05": 0.810,
  "engine_latency_ms": 342.5,
  "stable_block_ids": true,
  "blocks": [
    {
      "id": "b1a2c3d4",
      "type": "paragraph",
      "bbox": [120, 100, 980, 280],
      "reading_order": 1,
      "content": "Sanskrit text string here.\nSecond line continues here.",
      "confidence": 0.85,
      "language": "sa",
      "words": [
        {
          "text": "Sanskrit",
          "bbox": [120, 100, 190, 130],
          "confidence": 0.95
        }
      ]
    }
  ]
}
```

#### B. Key Fields Reference

* **`contract_version` (String, Required):** Must be `"2.2"` (or legacy `"2.1"` / `"2.0"`).
* **`page_width` / `page_height` (Integer, Required):** Dimensions (in pixels) of the source scan. Required so the spatial Replica view can scale and align bounding boxes precisely.
* **`page_confidence` (Float 0.0 - 1.0 or null):** Aggregate page quality score. May be `null` for confidence-blind VLM engines.
* **`page_p05` (Float 0.0 - 1.0 or null):** 5th-percentile token confidence floor. Serves as a robust indicator of low-confidence text runs. `null` for VLM engines.
* **`stable_block_ids` (Boolean, Optional):** Default `true`. Signals whether block IDs remain stable across re-runs.
* **`coordinate_space` (String, Optional):** Can be `"pixel"` (coordinates map directly to image pixels) or `"normalized"` (coordinates scaled between `0.0` and `1.0`). Defaults to `"pixel"`.
* **`blocks` (Array, Required):** List of recognized layout elements. Each block must have:
  * **`id` (String, Required):** A stable, page-unique identifier (e.g. 8 hex characters). Ensures manual edits by proofreaders survive a re-OCR run.
  * **`type` (String, Required):** Layout type. Valid values: `paragraph`, `heading`, `subheading`, `verse`, `table`, `figure`, `caption`, `footnote`, `running-header`, `page-number`, `column-header`, `equation`.
  * **`bbox` (Array, Required):** Array of four coordinates `[x1, y1, x2, y2]` denoting the bounding box of the block.
  * **`reading_order` (Integer, Required):** 1-based order in which the block should be read.
  * **`content` (String, Required):** Plain text inside the block. For the `table` type, this field contains a complete HTML `<table>` string instead of plain text.
  * **`confidence` (Float 0.0 - 1.0 or null):** Block recognition score. Scores `< 0.5` are highlighted in red (errors) and `0.5 - 0.74` in amber (review recommended).
  * **`words` (Array, Optional):** Word-level or line-level breakdown containing local text coordinates and confidence scores for in-block word-level highlights.

*If v2 blocks are missing from the response, Kalanjiyam falls back to legacy behaviors by parsing raw text and TSV bounding boxes if possible.*

---

## Troubleshooting & Container Logs

If you encounter issues during local development or production deployment, monitoring container logs and understanding the state of individual services is critical.

All eight main containers in the Docker Compose stack have explicit environment-suffixed `container_name` attributes (e.g. `kalanjiyam-web-dev` / `kalanjiyam-web-prod`), meaning you can access their logs directly using standard Docker commands or through the environment's orchestrator (Makefile/deploy script).

### General Logging Commands

* **View logs for all containers:**
  * **Local Development:**
    ```bash
    make docker-logs
    # or
    docker compose -p kalanjiyam-local -f deploy/local/docker-compose.yml logs -f
    ```
  * **Production Deployment:**
    ```bash
    ./deploy/prod/deploy.sh logs
    # or
    docker compose -p kalanjiyam-prod -f deploy/prod/docker-compose.yml logs -f
    ```
* **View logs for a specific container:**
  ```bash
  docker logs -f <container-name>
  ```
  *(Example: `docker logs -f kalanjiyam-web-dev` or `docker logs -f kalanjiyam-web-prod`)*

---

### Container Reference & Troubleshooting

#### 1. `kalanjiyam-web` (`kalanjiyam-web-dev` / `kalanjiyam-web-prod`)
* **Role:** Serves the main Flask web application (using Gunicorn in production, and standard Flask development server in local).
* **Logs Command:**
  ```bash
  docker logs -f kalanjiyam-web-dev
  ```
* **Common Issues:**
  * **502 Bad Gateway / Connection Refused:**
    * *Cause:* The Flask server failed to start or crashed during initialization.
    * *Troubleshooting:* Check the logs for Python traceback errors. Ensure all required environment variables in `.env` are defined and valid. Check if the database is reachable.
  * **Configuration validation errors on startup:**
    * *Cause:* A critical configuration variable like `FLASK_UPLOAD_FOLDER` is missing or is configured as a relative path instead of an absolute path.
    * *Troubleshooting:* Modify the `.env` file to use absolute paths and restart the service.

#### 2. `kalanjiyam-celery` (`kalanjiyam-celery-dev` / `kalanjiyam-celery-prod`)
* **Role:** Core Celery worker processing `default`, `ocr`, `low_priority`, and `search_index` task queues.
* **Logs Command:**
  ```bash
  docker logs -f kalanjiyam-celery-dev
  ```
* **Common Issues:**
  * **OCR / indexing tasks remain in "Pending" or fail instantly:**
    * *Cause:* The Celery container is either not running, cannot reach Redis, or cannot communicate with the external OCR service.
    * *Troubleshooting:* Verify the container is running with `docker ps`. Check the logs for connection timeout or host lookup failures (e.g. if `OCR_SERVICE_URL` is misconfigured).

#### 3. `kalanjiyam-celery-batch` (`kalanjiyam-celery-batch-dev` / `kalanjiyam-celery-batch-prod`)
* **Role:** Dedicated Celery worker for the `s3_batch` queue, managing large PDF splitting, S3 folder discovery, and parallel batch ingestion.
* **Logs Command:**
  ```bash
  docker logs -f kalanjiyam-celery-batch-dev
  ```
* **Common Issues:**
  * **Batch OCR tasks stuck:**
    * *Cause:* Worker disconnected from Redis or S3 gateway unreachable. Check `python cli.py batch-status --job-id <ID>` for failed item traces.

#### 4. `kalanjiyam-celery-metadata` (`kalanjiyam-celery-metadata-dev` / `kalanjiyam-celery-metadata-prod`)
* **Role:** Dedicated Celery worker for the `metadata` queue, performing token-budgeted whole-document archival metadata extraction.
* **Logs Command:**
  ```bash
  docker logs -f kalanjiyam-celery-metadata-dev
  ```
* **Common Issues:**
  * **Metadata extractions unqueued:**
    * *Cause:* Ensure worker is listening on `-Q metadata`. Check with `python cli.py metadata-runs`.

#### 5. `kalanjiyam-search` (`kalanjiyam-search-dev` / `kalanjiyam-search-prod`)
* **Role:** OpenSearch cluster with the ICU analysis plugin for multilingual tokenization and full-text search across library books and proofing projects.
* **Logs Command:**
  ```bash
  docker logs -f kalanjiyam-search-dev
  ```
* **Common Issues:**
  * **Search queries failing with 503 or yellow/red cluster health:**
    * *Cause:* OpenSearch heap exhaustion or uninitialized indices. Re-run `python cli.py search-index init` or `python cli.py search-index status`.

#### 6. `kalanjiyam-versitygw` (`kalanjiyam-versitygw-dev` / `kalanjiyam-versitygw-prod`)
* **Role:** Versity Gateway S3 adapter. It exposes the application's local filesystem upload storage through an S3-compatible API.
* **Logs Command:**
  ```bash
  docker logs -f kalanjiyam-versitygw-dev
  ```
* **Common Issues:**
  * **Upload or file storage errors (S3 API Connection Errors):**
    * *Cause:* The gateway failed to initialize POSIX storage or credentials mismatch between `.env` and Compose environment.
    * *Troubleshooting:* Inspect logs to verify VersityGW is listening on port `7070` and storage path permissions are correct.

#### 7. `kalanjiyam-redis` (`kalanjiyam-redis-dev` / `kalanjiyam-redis-prod`)
* **Role:** Redis container serving as the message broker and backend for Celery task queues and distributed locks.
* **Logs Command:**
  ```bash
  docker logs -f kalanjiyam-redis-dev
  ```

#### 8. `kalanjiyam-db` (`kalanjiyam-db-dev` / `kalanjiyam-db-prod`)
* **Role:** PostgreSQL 15 database container storing platform metadata, users, organizations, proofing logs, and books.
* **Logs Command:**
  ```bash
  docker logs -f kalanjiyam-db-dev
  ```
* **Common Issues:**
  * **Fatal Authentication Errors / Database Connection Refused:**
    * *Cause:* Credentials configured in `SQLALCHEMY_DATABASE_URI` do not match `POSTGRES_PASSWORD`.
    * *Troubleshooting:* Verify that passwords and usernames match in `.env`. Check migration status with `docker exec -it kalanjiyam-web-dev alembic current` and upgrade with `docker exec -it kalanjiyam-web-dev alembic upgrade head` (or `./deploy/prod/deploy.sh migrate`).

---

## Batch OCR Process (CLI)

Kalanjiyam supports ingesting and running OCR on massive batches of PDFs and raw image folders using the dedicated `batch-ocr` CLI command. This process avoids Out-of-Memory (OOM) errors and is fully tracked in the PostgreSQL database.

### Prerequisites
Before running the batch process, ensure the following `.env` variables are configured:

Ensure the Celery worker (`s3_batch` queue) is running to process the jobs:
```bash
docker exec -it kalanjiyam-celery celery -A kalanjiyam.tasks.app worker -Q s3_batch,celery -l info
```

### Usage

Run the command inside the web container using `python cli.py batch-ocr`.

**Basic S3 Bucket Scan (PDFs & Images):**
```bash
docker exec -it kalanjiyam-web python scripts/cli.py batch-ocr --s3-uri s3://my-bucket/target-folder/ --org "udaan"
```

**Local Directory Scan (Filtered for PDFs under an Organization):**
```bash
docker exec -it kalanjiyam-web python scripts/cli.py batch-ocr --local-uri /data/uploads/batch_pdfs/ --pdf --org "udaan"
```

**Available Options:**
* `--s3-uri <URI>`: Scans an S3 bucket path recursively.
* `--local-uri <PATH>`: Scans a local filesystem path recursively.
* `--org <SLUG>`: Attach created projects to a specific Organization by its URL slug (e.g. `udaan`). Fails if the organization doesn't exist.
* `--lang <LANG>`: Specify OCR language code (defaults to `eng`). Examples: `eng`, `tam`, `hin`.
* `--pdf`: Filter the discovery to only queue PDF documents.
* `--image`: Filter the discovery to only queue Image Folders (directories containing standard image formats).
* If neither `--pdf` nor `--image` is provided, it will process both.

### Listing & Checking Batch Jobs

**List recent batch jobs (ID, Status, Time, Time Taken, Target):**
```bash
docker exec -it kalanjiyam-web python scripts/cli.py batch-list
```

**Inspect a specific batch job (with full performance metrics):**
```bash
docker exec -it kalanjiyam-web python scripts/cli.py batch-status --job-id 1
```

**Cancel a pending or in-progress batch job:**
```bash
docker exec -it kalanjiyam-web python scripts/cli.py batch-cancel --job-id 1
```

**Retry failed or stuck items in a batch job:**
```bash
docker exec -it kalanjiyam-web python scripts/cli.py batch-retry --job-id 1 --org "udaan" --lang "eng"
```

### How it Works (Under the Hood)
1. **Discovery & Registration**: The CLI recursively lists all matching files/folders and registers them as `BatchItem` records in PostgreSQL.
2. **Celery Queueing**: Each item is dispatched to the `s3_batch` Celery worker queue.
3. **Download & Extraction**: The worker downloads the file. PDFs are memory-efficiently split into JPEGs using `PyMuPDF`. Image folders are standardized to JPEGs using `Pillow`. Temporary source files are automatically cleaned up from the host disk.
4. **Visual Cropping & Storage**: Page images are passed to the OCR API. If tables/figures are detected, the Visual Element Cropper extracts them and stores everything securely in the Kalanjiyam `Storage` backend.
5. **Database Sync**: Status tracking, metrics (file size, extraction latency, OCR latency, payload bytes), and parsed `OCRBlock` / `PageVersion` data are actively synchronized to PostgreSQL in real-time.

---

## Internationalization & Localization (i18n & l10n)

Kalanjiyam supports multiple Indian language interfaces as well as English using `Flask-Babel`.

### Supported Languages
* **English (`en`)** *(Default source)*
* **Tamil (`ta`)**
* **Hindi (`hi_IN`)**
* **Sanskrit (`sa`)**
* **Telugu (`te_IN`)**

### 1. Extract & Initialize Catalogs
To extract all translatable UI strings from Python and Jinja templates into `messages.pot` and initialize/update the `.po` catalogs:

```bash
# Run via Makefile
make init-i18n

# Or run the native Python module directly
python -m kalanjiyam.scripts.fetch_i18n_files
```

### 2. Auto-Translate UI Catalogs with LLM
Kalanjiyam includes an automated machine translation script (`kalanjiyam.scripts.translate_catalogs`) connected to the `llm-gemma` (26B) and `gemma` translation backends.

#### Basic Usage:
```bash
# Translate with llm-gemma (26B instruction-tuned model via OCR_SERVICE_URL)
python -m kalanjiyam.scripts.translate_catalogs --engine llm_gemma

# Or translate with gemma 12B (via TRANSLATION_SERVICE_URL)
python -m kalanjiyam.scripts.translate_catalogs --engine gemma
```

#### Key Behaviors & Flags:
* **Incremental Resume (Default)**: The script automatically skips strings that are already translated (`msgstr != ""`). If the translation process is interrupted (Ctrl+C) or encounters a network error, re-running the command safely resumes where it left off.
* **Force Re-Translation (`--force` / `-f`)**: Overwrites and re-translates all strings from scratch:
  ```bash
  python -m kalanjiyam.scripts.translate_catalogs --engine llm_gemma --force
  ```
* **Graceful Exit**: Pressing `Ctrl+C` at any time safely saves all translated entries and automatically compiles `messages.mo` before exiting.

### 3. Docker Staging & Production Commands

#### Run Translation in Container (with Proxy Bypass):
```bash
NO_PROXY="*" docker exec -it kalanjiyam-web-staging python -m kalanjiyam.scripts.translate_catalogs --engine llm_gemma
```

#### Sync Translated Catalogs to Host Repository:
```bash
cd ~/kalanjiyam-dev/kalanjiyam
docker exec kalanjiyam-web-staging tar -C /app/kalanjiyam -cf - translations | tar -xf -
```

#### Compile & Reload Web App:
```bash
# 1. Compile .po to .mo inside container
docker exec -it kalanjiyam-web-staging pybabel compile -d kalanjiyam/translations

# 2. Restart container to reload memory cache
docker restart kalanjiyam-web-staging
```