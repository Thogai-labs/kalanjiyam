from unittest.mock import patch

from kalanjiyam.queries import get_engine, get_session


def test_get_engine_sqlite(flask_app):
    """Test that get_engine on SQLite uses pool_pre_ping without errors."""
    with flask_app.app_context():
        engine = get_engine()
        assert engine.pool._pre_ping is True
        session = get_session()
        assert session is not None


def test_get_engine_postgresql_pooling(flask_app):
    """Test that get_engine on PostgreSQL configures QueuePool correctly."""
    with flask_app.app_context():
        with patch.dict(
            flask_app.config,
            {
                "SQLALCHEMY_DATABASE_URI": "postgresql://user:pass@localhost:5432/testdb",
                "DB_POOL_SIZE": 15,
                "DB_MAX_OVERFLOW": 25,
                "DB_POOL_RECYCLE": 3600,
                "DB_POOL_TIMEOUT": 45,
            },
        ):
            # Use __wrapped__ to test engine creation with PostgreSQL config without
            # mutating the session-cached in-memory test database engine.
            engine = get_engine.__wrapped__()
            assert engine.pool.size() == 15
            assert engine.pool._max_overflow == 25
            assert engine.pool._recycle == 3600
            assert engine.pool._timeout == 45
            assert engine.pool._pre_ping is True
            engine.dispose()
