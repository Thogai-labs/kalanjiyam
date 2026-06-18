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

4. Make sure in the `Makefile` (located at the root of the project) that `KALANJIYAM_DEPLOYMENT_ENV` is set to `local`.
5. Start the Docker services by running the following command:
   ```bash
   make docker-start
   ```
6. When the process finishes starting the services, you will see a success message:
   ```bash
   Container kalanjiyam-redis                     Started
   Container kalanjiyam-local-kalanjiyam-celery-1 Started
   Container kalanjiyam-web                       Started
   Container kalanjiyam-db                        Started
   Kalanjiyam WebApp   : ✔ 
   Kalanjiyam URL      : http://0.0.0.0:5000
   
   To stop, run "make docker-stop".
   ```
7. Visit the site at [http://localhost:5000](http://localhost:5000).

## Managing Users & Organizations via the CLI

All user and organization management is performed using the CLI tool. Inside the running web container `kalanjiyam-web`, the tool is located at `scripts/cli.py` (which corresponds to `cli.py` at the root of the host workspace). Run these commands as follows:

### 1. Create a Super Admin
If you need platform-wide administrator privileges to manage settings, organizations, and all other accounts, run:
```bash
docker exec -it kalanjiyam-web python scripts/cli.py create-super-admin
```
*(Note: Only one super admin account is allowed on the platform).*

### 2. Create a New Organization (Tenant)
```bash
docker exec -it kalanjiyam-web python scripts/cli.py create-organization --name "My Org" --slug "my-org"
```

### 3. Create a Regular User inside an Organization
To create a new user account linked to a specific organization (defaults to the `p1` basic proofer role):
```bash
docker exec -it kalanjiyam-web python scripts/cli.py create-org-user --org "my-org" --username "test_user" --email "user@example.com"
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

## Database Schema Overview

Kalanjiyam's data models are managed dynamically using SQLAlchemy. Below is the complete entity-relationship diagram of all 28 tables defined across `/models/`:

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
    }
    roles {
        int id PK
        string name
        datetime created_at
    }
    user_roles {
        int user_id PK, FK "users.id"
        int role_id PK, FK "roles.id"
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
        int admin_user_id FK "users.id"
        datetime created_at
        datetime updated_at
    }
    user_groups {
        int user_id PK, FK "users.id"
        int group_id PK, FK "groups.id"
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
        string display_title
        string print_title
        string author
        string editor
        string publisher
        string publication_year
        string worldcat_link
        text description
        text notes
        text page_numbers
        datetime created_at
        datetime updated_at
        int board_id FK "discussion_boards.id"
        int creator_id FK "users.id"
        int genre_id FK "genres.id"
        boolean is_publicly_viewable
    }
    project_groups {
        int group_id PK, FK "groups.id"
        int project_id PK, FK "proof_projects.id"
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
    proof_revisions {
        int id PK
        int project_id FK "proof_projects.id"
        int page_id FK "proof_pages.id"
        int author_id FK "users.id"
        int status_id FK "proof_page_statuses.id"
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

    %% Library Texts System
    texts {
        int id PK
        string slug
        string title
        text header
    }
    text_groups {
        int group_id PK, FK "groups.id"
        int text_id PK, FK "texts.id"
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

    %% Blog
    blog_posts {
        int id PK
        int author_id FK "users.id"
        datetime created_at
        datetime updated_at
        string title
        string slug
        text content
    }

    %% Site/Sponsorship
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

    %% Relationships
    %% Auth
    users ||--o{ user_roles : "has roles"
    roles ||--o{ user_roles : "assigned to"
    users ||--o{ auth_password_reset_tokens : "requests"

    %% Groups
    groups ||--o{ users : "has primary members"
    users ||--o{ user_groups : "member of"
    groups ||--o{ user_groups : "contains"
    groups ||--o{ text_groups : "has access to texts"
    texts ||--o{ text_groups : "accessible by groups"
    groups ||--o{ project_groups : "owns proof projects"
    proof_projects ||--o{ project_groups : "owned by groups"

    %% Discussion (Talk)
    discussion_boards ||--o{ discussion_threads : "contains"
    users ||--o{ discussion_threads : "creates"
    discussion_boards ||--o{ discussion_posts : "contains"
    discussion_threads ||--o{ discussion_posts : "contains"
    users ||--o{ discussion_posts : "writes"

    %% Proofing
    users ||--o{ proof_projects : "creates"
    discussion_boards ||--o{ proof_projects : "associated with"
    genres ||--o{ proof_projects : "categorizes"
    proof_projects ||--o{ proof_pages : "contains"
    proof_page_statuses ||--o{ proof_pages : "defines status of"
    proof_projects ||--o{ proof_revisions : "tracks revisions for"
    proof_pages ||--o{ proof_revisions : "has history of"
    users ||--o{ proof_revisions : "submits"
    proof_page_statuses ||--o{ proof_revisions : "status during revision"
    proof_pages ||--o{ proof_translations : "translated from"
    proof_revisions ||--o{ proof_translations : "revision translated"
    users ||--o{ proof_translations : "translates"
    proof_projects ||--o{ proof_ocr_comparisons : "compared in"

    %% Library Texts
    texts ||--o{ text_sections : "has divisions"
    texts ||--o{ text_blocks : "contains blocks"
    text_sections ||--o{ text_blocks : "contains blocks in section"
    texts ||--o{ block_parses : "has parse data for"
    text_blocks ||--o{ block_parses : "parsed in"

    %% Dictionaries
    dictionaries ||--o{ dictionary_entries : "contains entries"

    %% Blog
    users ||--o{ blog_posts : "authors"
```

*(Note: These correspond directly to the database tables mapped by SQLAlchemy models in the [kalanjiyam/models/](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/models/) directory).*

### Table Summary:

1. **`users`**: Platform user accounts storing credentials, status flags (verified, deleted, banned), and default organization association.
2. **`roles`**: Permissions scopes (e.g. `p1`, `p2`, `moderator`, `org_admin`, `super_admin`).
3. **`user_roles`**: Many-to-many lookup connecting users with their system roles.
4. **`auth_password_reset_tokens`**: Stores hashed tokens for security recovery operations.
5. **`groups`**: Tenants/organizations managing resource quotas, limits, and administrative mappings.
6. **`user_groups`**: Many-to-many membership linking users to their secondary groups.
7. **`texts`**: Digital library base documents featuring meta-header definitions (TEI).
8. **`text_sections`**: Hierarchical subdivisions of library texts (e.g. chapters, cantos).
9. **`text_blocks`**: Reusable text fragments (e.g. verses, paragraphs) containing raw XML.
10. **`text_groups`**: Association table mapping text access permissions to tenant groups.
11. **`block_parses`**: Lexical/grammatical analysis strings associated with library text blocks.
12. **`genres`**: Categories (e.g. Kavya, Shastra) to classify proofreading projects.
13. **`proof_projects`**: Tracking elements representing books in the proofreading queue.
14. **`project_groups`**: Association table mapping projects to their owning organizations.
15. **`proof_pages`**: Individual page records containing OCR bounding boxes and images.
16. **`proof_page_statuses`**: Enumerated validation status types (`reviewed-0`, `reviewed-1`, etc.).
17. **`proof_revisions`**: Transcription edit history recording plain-text and structured documents.
18. **`proof_translations`**: Keeps translations of revisions across languages and engines (e.g., GPT, Google).
19. **`proof_ocr_comparisons`**: Analytics for comparing OCR engine results against manual proofing ground truth.
20. **`discussion_boards`**: Associated forum board instances.
21. **`discussion_threads`**: Forum topic structures created by users.
22. **`discussion_posts`**: Thread comments/posts compiled under a forum thread.
23. **`blog_posts`**: Announcements and updates authored by system operators.
24. **`site_project_sponsorship`**: Public donation goals to support book digitizations.
25. **`contributor_info`**: Public recognition list for contributors and moderators.
26. **`dictionaries`**: Lexicon definitions mapping to various languages.
27. **`dictionary_entries`**: Lexical index mappings containing value definitions in XML.
28. **`alembic_version`**: Schema migration states tracked internally by Alembic.

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
* Ensure it is running on port `5001` (or your configured port).
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