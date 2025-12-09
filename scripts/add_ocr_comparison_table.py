import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

from kalanjiyam import queries as q
from kalanjiyam.models.proofing import OCRComparison
from config import create_config_only_app

def create_table():
    # Load config to set up DB URI
    # Assuming development environment for now, or use os.getenv('FLASK_ENV', 'development')
    env = os.getenv('FLASK_ENV', 'development')
    print(f"Using environment: {env}")
    app = create_config_only_app(env)
    
    with app.app_context():
        engine = q.get_engine()
        print(f"Creating table {OCRComparison.__tablename__}...")
        OCRComparison.__table__.create(engine, checkfirst=True)
        print("Done.")

if __name__ == "__main__":
    create_table()

