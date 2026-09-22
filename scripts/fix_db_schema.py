#!/usr/bin/env python3
"""Helper script to instantly add missing batch metrics columns directly to PostgreSQL database tables."""

import kalanjiyam
from kalanjiyam import database as db

statements = [
    "ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS extracted_images_size_bytes INTEGER;",
    "ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS cropped_images_size_bytes INTEGER;",
    "ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS ocr_data_size_bytes INTEGER;",
    "ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS translation_data_size_bytes INTEGER;",
    "ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS source_lang VARCHAR(32);",
    "ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS target_lang VARCHAR(32);",
    "ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS total_translation_latency_ms DOUBLE PRECISION;",

    "ALTER TABLE batch_ocr_pages ADD COLUMN IF NOT EXISTS ocr_latency_ms DOUBLE PRECISION;",
    "ALTER TABLE batch_ocr_pages ADD COLUMN IF NOT EXISTS translation_latency_ms DOUBLE PRECISION;",
    "ALTER TABLE batch_ocr_pages ADD COLUMN IF NOT EXISTS extracted_image_size_bytes INTEGER;",
    "ALTER TABLE batch_ocr_pages ADD COLUMN IF NOT EXISTS cropped_image_size_bytes INTEGER;",
    "ALTER TABLE batch_ocr_pages ADD COLUMN IF NOT EXISTS ocr_data_size_bytes INTEGER;",
    "ALTER TABLE batch_ocr_pages ADD COLUMN IF NOT EXISTS translation_data_size_bytes INTEGER;",
    "ALTER TABLE batch_ocr_pages ADD COLUMN IF NOT EXISTS source_lang VARCHAR(32);",
    "ALTER TABLE batch_ocr_pages ADD COLUMN IF NOT EXISTS target_lang VARCHAR(32);",

    "ALTER TABLE proof_projects ADD COLUMN IF NOT EXISTS folder VARCHAR;",
    "ALTER TABLE proof_projects ADD COLUMN IF NOT EXISTS tags JSON;",
    """CREATE TABLE IF NOT EXISTS proof_folders (
        id SERIAL PRIMARY KEY,
        path VARCHAR NOT NULL UNIQUE,
        name VARCHAR NOT NULL,
        parent_path VARCHAR NOT NULL DEFAULT '',
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
        creator_id INTEGER REFERENCES users(id),
        fingerprint_id VARCHAR
    );""",
    "CREATE INDEX IF NOT EXISTS ix_proof_folders_path ON proof_folders (path);",
    "CREATE INDEX IF NOT EXISTS ix_proof_folders_parent_path ON proof_folders (parent_path);",
]

def main():
    app = kalanjiyam.create_app()
    with app.app_context():
        with db.engine.connect() as conn:
            for stmt in statements:
                conn.execute(db.text(stmt))
            conn.commit()
        print("Successfully updated database schema columns for batch_items and batch_ocr_pages!")

if __name__ == "__main__":
    main()
