#!/usr/bin/env python3
"""Initialize the database with seed data."""

import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from kalanjiyam import config
from kalanjiyam import database as db
from kalanjiyam.seed import dcs, lookup, texts

# Import all seed modules to ensure they're registered
from kalanjiyam.seed.dictionaries import monier  # noqa
from kalanjiyam.seed.dictionaries import amarakosha  # noqa
from kalanjiyam.seed.dictionaries import apte  # noqa
from kalanjiyam.seed.texts import gretil  # noqa


def main():
    """Initialize the database."""
    config_obj = config.load_config_object("development")
    
    # Create database tables
    db.create_all()
    
    print("Database initialized successfully!")


if __name__ == "__main__":
    main()
