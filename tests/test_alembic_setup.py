"""Tests for Alembic migration setup."""

from pathlib import Path


def test_alembic_ini_exists():
    assert Path("alembic.ini").exists()


def test_migrations_dir_exists():
    assert Path("migrations").is_dir()


def test_initial_migration_exists():
    assert Path("migrations/versions/001_initial_schema.py").exists()


def test_env_py_exists():
    assert Path("migrations/env.py").exists()
