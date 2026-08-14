"""The migrations must describe exactly the schema the models declare."""
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]


def _alembic(*args, db_path: Path):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "AUTH_SECRET": "test-only-secret-not-used-anywhere-else",
        "PASSWORD_HASH_ROUNDS": "1000",
        "HOME": str(API_ROOT),
    }
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          cwd=API_ROOT, env=env, capture_output=True, text=True)


def test_migrations_apply_from_empty(tmp_path):
    result = _alembic("upgrade", "head", db_path=tmp_path / "m.db")
    assert result.returncode == 0, result.stderr


def test_no_schema_drift_between_models_and_migrations(tmp_path):
    """Catches the failure where a model gained a column and nobody wrote the
    migration: everything passes locally against create_all, then production
    breaks on the first query."""
    db = tmp_path / "drift.db"
    assert _alembic("upgrade", "head", db_path=db).returncode == 0
    result = _alembic("check", db_path=db)
    assert result.returncode == 0, (
        "Models and migrations have diverged. Run:\n"
        "  alembic revision --autogenerate -m 'describe the change'\n\n"
        + result.stdout + result.stderr)


def test_downgrade_is_reversible(tmp_path):
    db = tmp_path / "down.db"
    assert _alembic("upgrade", "head", db_path=db).returncode == 0
    assert _alembic("downgrade", "base", db_path=db).returncode == 0
    assert _alembic("upgrade", "head", db_path=db).returncode == 0
