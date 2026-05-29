import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

from config import create_config_only_app
from kalanjiyam import queries as q
from kalanjiyam.models.proofing import OCRComparison


def create_table():
    env = os.getenv("FLASK_ENV", "development")
    print(f"Using environment: {env}")
    app = create_config_only_app(env)

    with app.app_context():
        engine = q.get_engine()
        print(f"Creating table {OCRComparison.__tablename__}...")
        OCRComparison.__table__.create(engine, checkfirst=True)
        print("Done.")


if __name__ == "__main__":
    create_table()
