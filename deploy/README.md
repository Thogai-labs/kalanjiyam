## Deployment environments

| Directory | Environment | Container Suffix | Default Web Port | Storage | DB password |
|-----------|-------------|------------------|------------------|---------|-------------|
| `deploy/local/` | Local Docker dev | `-dev` | `5002` | S3 via versitygw (named volume) | hardcoded `kalanjiyam` |
| `deploy/staging/` | CI / staging | `-staging` | `5001` | `local` (no S3 gateway) | hardcoded `kalanjiyam` |
| `deploy/prod/` | Production Docker | `-prod` | `5000` | S3 via versitygw (`~/kalanjiyam-data/uploads`) | `POSTGRES_PASSWORD` from `.env` |

All three read `../../.env` via `env_file`. Configure `.env` from `.env.example` before starting.

**Production** — use `deploy/prod/deploy.sh` (validates `.env`, builds image, runs migrations, starts services):

```bash
cp .env.example .env   # fill in all required values
./deploy/prod/deploy.sh
./deploy/prod/deploy.sh logs     # tail logs
./deploy/prod/deploy.sh restart  # restart without rebuild
./deploy/prod/deploy.sh stop     # tear down
```

**Staging** — use `deploy/staging/deploy.sh`:

```bash
./deploy/staging/deploy.sh
./deploy/staging/deploy.sh logs     # tail logs
./deploy/staging/deploy.sh stop     # tear down
```

**Local / Dev** — use `deploy/local/deploy.sh` (or `make docker-start`):

```bash
./deploy/local/deploy.sh
./deploy/local/deploy.sh logs     # tail logs
./deploy/local/deploy.sh stop     # tear down
```

Celery listens on `default` and `ocr` queues in all environments. OCR runs as a separate
service; set `OCR_SERVICE_URL` to a host reachable from inside containers.

Full guide: `docs/production-deploy.rst`.

## File storage

Uploads (source PDFs, page images, editor images) go through an S3-compatible
storage layer (`kalanjiyam/utils/storage.py`). The backend is chosen by config:

| `.env` key | Values | Notes |
|------------|--------|-------|
| `STORAGE_BACKEND` | `s3` (default in compose) or `local` | `local` writes directly under `FLASK_UPLOAD_FOLDER` |
| `S3_ENDPOINT_URL` | URL | Set by compose to the bundled gateway; point at MinIO/SeaweedFS/Ceph/AWS to swap backends |
| `S3_BUCKET` | name | Default `uploads` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | strings | Required when `STORAGE_BACKEND=s3` |
| `S3_REGION` | region | Optional; self-hosted gateways ignore it |
| `S3_PUBLIC_ENDPOINT_URL` | URL | Optional; if set, page images redirect to presigned URLs instead of streaming through Flask |

The bundled gateway is [Versity Gateway](https://github.com/versity/versitygw)
(Apache 2.0), running in POSIX mode over the existing data directory. Files on
disk stay plain files: in prod, `~/kalanjiyam-data/uploads/` is exposed as the
`uploads` bucket, so **pre-existing uploads need no migration**. To move a
deployment to a different S3 backend later, sync the objects (`rclone sync` /
`aws s3 sync`) and change the endpoint/credential values — no code changes.

## How it works?

```mermaid
graph LR

    subgraph Environment
    os(Ubuntu/MacOS) 
    os --> db
    db -->|db| kalanjiyam   
    
    subgraph Setup
    db[("SQLite")]
    texts[GRETIL, DCS, dictionaries, ...]
    end
    style Setup fill:#ff7621,stroke:#fff,stroke-width:4px

    
    subgraph Deploy
    kalanjiyam(Kalanjiyam container) & celerey[Celery] & redis[Redis]
    end 
    style Deploy fill:#f3cf26, stroke:#fff, stroke-width:4px
    end
    style Environment fill:#edf7f6

    kalanjiyam --> browser(https://your-domain.com)
```

## What is the PR process?

```mermaid
flowchart LR
    contributor(Contributor Fork)    
    ghact(Github Actions)
    pre(py-lint, js-lint)
    build(Docker build & publish)
    post(py-tests, js-tests, system tests)
    subgraph Main-Branch
        code(PR on main)
        code-->open(PR open/sync)
        open-->Main-GithubActions
        
        subgraph Main-GithubActions
            pre-->ghact
            build-->ghact
            post-->ghact
        end
    end

    subgraph Approve
        approve(Reviewer merges PR)
    end

    rel-ghact(Github Actions)
    rel-pre(py-lint, js-lint)
    rel-build(Docker build & publish)
    rel-post(py-tests, js-tests, system tests)
    rel-staging(Deploy to Staging)
    subgraph Release-Branch
        rel-pr("PR on release (check every 5 min.s)")
        rel-pr-->rel-pr-open(PR open/sync)
        rel-pr-open-->Release-GithubActions
        subgraph Release-GithubActions
            rel-pre-->rel-ghact
            rel-build-->rel-ghact
            rel-post-->rel-ghact
            rel-staging-->rel-ghact
        end
    end
    
    subgraph Rel-Merge-GithubActions
        rel-staging-down(Teardown staging)
    end
    
    style Main-Branch fill:#b2b2b2, stroke:#fff, stroke-width:4px
    style Approve fill:#a2a2a2
    style Release-Branch fill:#c2c2c2, stroke:#fff, stroke-width:4px
    style Rel-Merge-GithubActions fill:#d1d1d1, stroke:#fff, stroke-width:4px
    contributor-->Main-Branch    
    Main-Branch-->check{Passed?}
    check-->|Yes|Approve    
    Approve-->Release-Branch
    Release-Branch-->rel-check{Passed?}
    rel-check-->|Yes|rel-merge(Reviewer merges PR)
    rel-merge-->rel-staging-down(Teardown Staging)
```
