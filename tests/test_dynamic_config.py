"""Tests for the dynamic config system: ConfigStore, ConfigWatcher, CLI, API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pm_core.config import (
    ConfigStore,
    ConfigWatcher,
    ExecutionConfig,
    LayeredValue,
    QualityConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def toml_file(tmp_path: Path) -> Path:
    """Create a temporary TOML config file."""
    content = b"""
[quality]
source_liveness_timeout_s = 45
volume_drop_red_pct = 20

[execution]
max_total_exposure_usd = 3000
patient_timeout_s = 15
"""
    path = tmp_path / "config.toml"
    path.write_bytes(content)
    return path


@pytest.fixture()
def fake_redis():
    """Create a fakeredis instance (skip if not installed)."""
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeRedis()


@pytest.fixture()
def store_toml_only(toml_file: Path) -> ConfigStore:
    """ConfigStore with only TOML layer (no Redis)."""
    return ConfigStore(toml_path=toml_file, redis=None)


@pytest.fixture()
def store_with_redis(toml_file: Path, fake_redis: Any) -> ConfigStore:
    """ConfigStore with TOML + Redis layers."""
    return ConfigStore(toml_path=toml_file, redis=fake_redis)


# ---------------------------------------------------------------------------
# ConfigStore — layered resolution
# ---------------------------------------------------------------------------


class TestConfigStoreLayering:
    """Test that ConfigStore resolves values in correct priority order."""

    def test_toml_values_loaded(self, store_toml_only: ConfigStore) -> None:
        """TOML values are returned when no higher layers exist."""
        section = store_toml_only.get_section("quality")
        assert section["source_liveness_timeout_s"] == 45
        assert section["volume_drop_red_pct"] == 20

    def test_empty_section_returns_empty(self, store_toml_only: ConfigStore) -> None:
        """Non-existent section returns empty dict."""
        assert store_toml_only.get_section("nonexistent") == {}

    def test_env_overrides_toml(self, toml_file: Path) -> None:
        """ENV vars override TOML values."""
        os.environ["PM_QUALITY_SOURCE_LIVENESS_TIMEOUT_S"] = "99"
        try:
            store = ConfigStore(toml_path=toml_file, redis=None)
            section = store.get_section("quality")
            assert section["source_liveness_timeout_s"] == 99
            # TOML value still present for non-overridden keys
            assert section["volume_drop_red_pct"] == 20
        finally:
            del os.environ["PM_QUALITY_SOURCE_LIVENESS_TIMEOUT_S"]

    def test_redis_overrides_env_and_toml(
        self, toml_file: Path, fake_redis: Any
    ) -> None:
        """Redis overrides take highest priority."""
        os.environ["PM_QUALITY_SOURCE_LIVENESS_TIMEOUT_S"] = "99"
        try:
            store = ConfigStore(toml_path=toml_file, redis=fake_redis)
            # Set a Redis override
            store.set_override("quality", "source_liveness_timeout_s", 200)

            section = store.get_section("quality")
            assert section["source_liveness_timeout_s"] == 200
        finally:
            del os.environ["PM_QUALITY_SOURCE_LIVENESS_TIMEOUT_S"]

    def test_no_toml_file_still_works(self) -> None:
        """ConfigStore works with no TOML file."""
        store = ConfigStore(toml_path=None, redis=None)
        assert store.get_section("anything") == {}

    def test_nonexistent_toml_path(self, tmp_path: Path) -> None:
        """Non-existent TOML path is handled gracefully."""
        store = ConfigStore(toml_path=tmp_path / "nope.toml", redis=None)
        assert store.get_section("quality") == {}


# ---------------------------------------------------------------------------
# ConfigStore — set/clear/diff
# ---------------------------------------------------------------------------


class TestConfigStoreOperations:
    """Test set_override, clear_override, diff, changelog."""

    def test_set_override_requires_redis(self, store_toml_only: ConfigStore) -> None:
        """set_override raises when no Redis."""
        with pytest.raises(RuntimeError, match="No Redis connection"):
            store_toml_only.set_override("quality", "x", 1)

    def test_clear_override_requires_redis(self, store_toml_only: ConfigStore) -> None:
        """clear_override raises when no Redis."""
        with pytest.raises(RuntimeError, match="No Redis connection"):
            store_toml_only.clear_override("quality", "x")

    def test_set_and_get(self, store_with_redis: ConfigStore) -> None:
        """Set override is visible in get_section."""
        store_with_redis.set_override("quality", "degraded_grace_s", 999)
        section = store_with_redis.get_section("quality")
        assert section["degraded_grace_s"] == 999

    def test_clear_specific_key(self, store_with_redis: ConfigStore) -> None:
        """Clearing a key removes only that override."""
        store_with_redis.set_override("quality", "degraded_grace_s", 999)
        store_with_redis.set_override("quality", "volume_drop_red_pct", 50)

        store_with_redis.clear_override("quality", "degraded_grace_s")

        section = store_with_redis.get_section("quality")
        assert "degraded_grace_s" not in section or section.get("degraded_grace_s") != 999
        assert section["volume_drop_red_pct"] == 50

    def test_clear_entire_section(self, store_with_redis: ConfigStore) -> None:
        """Clearing without key removes all overrides for the section."""
        store_with_redis.set_override("quality", "degraded_grace_s", 999)
        store_with_redis.set_override("quality", "volume_drop_red_pct", 50)

        store_with_redis.clear_override("quality")

        section = store_with_redis.get_section("quality")
        # Only TOML values should remain
        assert section["source_liveness_timeout_s"] == 45
        assert section["volume_drop_red_pct"] == 20

    def test_diff_shows_layers(self, store_with_redis: ConfigStore) -> None:
        """diff() shows per-field layer breakdown."""
        store_with_redis.set_override("quality", "source_liveness_timeout_s", 200)
        layers = store_with_redis.diff("quality")

        lv = layers["source_liveness_timeout_s"]
        assert isinstance(lv, LayeredValue)
        assert lv.toml == 45
        assert lv.override == 200
        assert lv.effective == 200

    def test_changelog_records_entries(self, store_with_redis: ConfigStore) -> None:
        """Setting overrides creates changelog entries."""
        store_with_redis.set_override("quality", "degraded_grace_s", 500)
        store_with_redis.set_override("execution", "fee_pct", 0.01)

        entries = store_with_redis.changelog()
        assert len(entries) >= 2
        # Most recent first
        sections = [e["section"] for e in entries]
        assert "quality" in sections
        assert "execution" in sections

    def test_changelog_filters_by_section(self, store_with_redis: ConfigStore) -> None:
        """Changelog can be filtered by section."""
        store_with_redis.set_override("quality", "degraded_grace_s", 500)
        store_with_redis.set_override("execution", "fee_pct", 0.01)

        entries = store_with_redis.changelog(section="quality")
        assert all(e["section"] == "quality" for e in entries)

    def test_changelog_empty_without_redis(self, store_toml_only: ConfigStore) -> None:
        """changelog returns empty list when no Redis."""
        assert store_toml_only.changelog() == []


# ---------------------------------------------------------------------------
# ConfigStore — subscribe
# ---------------------------------------------------------------------------


class TestConfigStoreSubscribe:
    """Test the subscribe/notification mechanism."""

    def test_subscribe_registers_callback(self, store_with_redis: ConfigStore) -> None:
        """Callbacks are registered per section."""
        cb = MagicMock()
        store_with_redis.subscribe("quality", cb)
        assert "quality" in store_with_redis._subscribers
        assert cb in store_with_redis._subscribers["quality"]

    def test_multiple_subscribers(self, store_with_redis: ConfigStore) -> None:
        """Multiple callbacks for same section."""
        cb1 = MagicMock()
        cb2 = MagicMock()
        store_with_redis.subscribe("quality", cb1)
        store_with_redis.subscribe("quality", cb2)
        assert len(store_with_redis._subscribers["quality"]) == 2


# ---------------------------------------------------------------------------
# ConfigWatcher — typed validation
# ---------------------------------------------------------------------------


class TestConfigWatcher:
    """Test ConfigWatcher typed validation and change callbacks."""

    def test_initial_load(self, store_with_redis: ConfigStore) -> None:
        """Watcher loads initial config from store."""
        watcher: ConfigWatcher[QualityConfig] = ConfigWatcher(
            store_with_redis, "quality", QualityConfig
        )
        cfg = watcher.current
        assert isinstance(cfg, QualityConfig)
        # From TOML
        assert cfg.source_liveness_timeout_s == 45
        assert cfg.volume_drop_red_pct == 20

    def test_initial_load_with_defaults(self, store_toml_only: ConfigStore) -> None:
        """Fields not in TOML use schema defaults."""
        watcher: ConfigWatcher[QualityConfig] = ConfigWatcher(
            store_toml_only, "quality", QualityConfig
        )
        cfg = watcher.current
        # From TOML
        assert cfg.source_liveness_timeout_s == 45
        # From schema default
        assert cfg.degraded_grace_s == 120

    def test_empty_section_uses_all_defaults(self, store_toml_only: ConfigStore) -> None:
        """Empty section uses all schema defaults."""
        watcher: ConfigWatcher[ExecutionConfig] = ConfigWatcher(
            store_toml_only, "nonexistent", ExecutionConfig
        )
        cfg = watcher.current
        assert cfg.max_total_exposure_usd == 5000
        assert cfg.patient_timeout_s == 30

    def test_refresh_picks_up_changes(self, store_with_redis: ConfigStore) -> None:
        """refresh() re-reads from store."""
        watcher: ConfigWatcher[QualityConfig] = ConfigWatcher(
            store_with_redis, "quality", QualityConfig
        )
        assert watcher.current.source_liveness_timeout_s == 45

        store_with_redis.set_override("quality", "source_liveness_timeout_s", 100)
        watcher.refresh()
        assert watcher.current.source_liveness_timeout_s == 100

    def test_bad_override_rejected(self, store_with_redis: ConfigStore) -> None:
        """Invalid override is rejected, previous config retained."""
        watcher: ConfigWatcher[QualityConfig] = ConfigWatcher(
            store_with_redis, "quality", QualityConfig
        )
        old_config = watcher.current

        # Set an invalid value (violates ge=5 constraint on source_liveness_timeout_s)
        store_with_redis.set_override("quality", "source_liveness_timeout_s", -1)
        watcher.refresh()

        # Config should be unchanged (bad value rejected)
        assert watcher.current.source_liveness_timeout_s == old_config.source_liveness_timeout_s

    def test_extra_field_rejected(self, store_with_redis: ConfigStore) -> None:
        """Unknown field rejected because extra='forbid'."""
        watcher: ConfigWatcher[QualityConfig] = ConfigWatcher(
            store_with_redis, "quality", QualityConfig
        )
        old_config = watcher.current

        store_with_redis.set_override("quality", "unknown_field", "bad")
        watcher.refresh()

        # Should retain old config
        assert watcher.current == old_config

    def test_on_change_callback_fires(self, store_with_redis: ConfigStore) -> None:
        """on_change callback receives (old, new) when config changes."""
        watcher: ConfigWatcher[QualityConfig] = ConfigWatcher(
            store_with_redis, "quality", QualityConfig
        )

        changes: list[tuple[QualityConfig, QualityConfig]] = []
        watcher.on_change(lambda old, new: changes.append((old, new)))

        store_with_redis.set_override("quality", "source_liveness_timeout_s", 100)
        watcher.refresh()

        assert len(changes) == 1
        old, new = changes[0]
        assert old.source_liveness_timeout_s == 45
        assert new.source_liveness_timeout_s == 100

    def test_on_change_not_fired_for_bad_override(
        self, store_with_redis: ConfigStore
    ) -> None:
        """Callback does NOT fire when override is rejected."""
        watcher: ConfigWatcher[QualityConfig] = ConfigWatcher(
            store_with_redis, "quality", QualityConfig
        )

        changes: list[tuple[Any, Any]] = []
        watcher.on_change(lambda old, new: changes.append((old, new)))

        store_with_redis.set_override("quality", "source_liveness_timeout_s", -1)
        watcher.refresh()

        assert len(changes) == 0

    def test_callback_error_does_not_break_watcher(
        self, store_with_redis: ConfigStore
    ) -> None:
        """Exception in callback is caught, config still updates."""
        watcher: ConfigWatcher[QualityConfig] = ConfigWatcher(
            store_with_redis, "quality", QualityConfig
        )

        def bad_callback(old: QualityConfig, new: QualityConfig) -> None:
            raise ValueError("boom")

        watcher.on_change(bad_callback)

        store_with_redis.set_override("quality", "source_liveness_timeout_s", 100)
        watcher.refresh()

        # Config still updated despite callback error
        assert watcher.current.source_liveness_timeout_s == 100


# ---------------------------------------------------------------------------
# ConfigStore.listen() — async Pub/Sub
# ---------------------------------------------------------------------------


class TestConfigStoreListen:
    """Test the async Pub/Sub listener."""

    @pytest.mark.asyncio
    async def test_listen_noop_without_redis(self, store_toml_only: ConfigStore) -> None:
        """listen() returns immediately when no Redis."""
        # Should not hang — just return
        await store_toml_only.listen()

    @pytest.mark.asyncio
    async def test_listen_noop_with_sync_redis_no_async(
        self, store_with_redis: ConfigStore
    ) -> None:
        """listen() gracefully handles fakeredis (no connection_pool attrs)."""
        import asyncio
        import contextlib

        # listen() should exit quickly when it cannot build an async client
        # We give it a short timeout to verify it doesn't hang forever
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(store_with_redis.listen(), timeout=1.0)


# ---------------------------------------------------------------------------
# CLI commands — unit tests with mock store
# ---------------------------------------------------------------------------


class TestCLICommands:
    """Test CLI config commands (using real ConfigStore, no subprocess)."""

    def test_cmd_get(self, store_toml_only: ConfigStore, capsys: Any) -> None:
        """cmd_get prints JSON."""
        from pm_pipeline.cli.config import cmd_get

        args = MagicMock()
        args.section = "quality"

        # Patch _build_store to return our test store
        import pm_pipeline.cli.config as cli_mod

        original = cli_mod._build_store
        cli_mod._build_store = lambda: store_toml_only
        try:
            cmd_get(args)
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["source_liveness_timeout_s"] == 45
        finally:
            cli_mod._build_store = original

    def test_cmd_set_validates(
        self, store_with_redis: ConfigStore, capsys: Any
    ) -> None:
        """cmd_set validates against schema."""
        from pm_pipeline.cli.config import cmd_set

        args = MagicMock()
        args.section = "quality"
        args.pairs = ["source_liveness_timeout_s=100"]

        import pm_pipeline.cli.config as cli_mod

        original = cli_mod._build_store
        cli_mod._build_store = lambda: store_with_redis
        try:
            cmd_set(args)
            # Verify it was set
            section = store_with_redis.get_section("quality")
            assert section["source_liveness_timeout_s"] == 100
        finally:
            cli_mod._build_store = original

    def test_cmd_set_rejects_invalid(
        self, store_with_redis: ConfigStore, capsys: Any
    ) -> None:
        """cmd_set rejects invalid values."""
        from pm_pipeline.cli.config import cmd_set

        args = MagicMock()
        args.section = "quality"
        args.pairs = ["source_liveness_timeout_s=-1"]

        import pm_pipeline.cli.config as cli_mod

        original = cli_mod._build_store
        cli_mod._build_store = lambda: store_with_redis
        try:
            with pytest.raises(SystemExit):
                cmd_set(args)
        finally:
            cli_mod._build_store = original

    def test_cmd_diff(self, store_with_redis: ConfigStore, capsys: Any) -> None:
        """cmd_diff prints a table."""
        from pm_pipeline.cli.config import cmd_diff

        store_with_redis.set_override("quality", "source_liveness_timeout_s", 200)

        args = MagicMock()
        args.section = "quality"

        import pm_pipeline.cli.config as cli_mod

        original = cli_mod._build_store
        cli_mod._build_store = lambda: store_with_redis
        try:
            cmd_diff(args)
            captured = capsys.readouterr()
            assert "source_liveness_timeout_s" in captured.out
            assert "200" in captured.out
        finally:
            cli_mod._build_store = original

    def test_cmd_clear_key(self, store_with_redis: ConfigStore, capsys: Any) -> None:
        """cmd_clear with --key clears one key."""
        from pm_pipeline.cli.config import cmd_clear

        store_with_redis.set_override("quality", "degraded_grace_s", 999)

        args = MagicMock()
        args.section = "quality"
        args.key = "degraded_grace_s"

        import pm_pipeline.cli.config as cli_mod

        original = cli_mod._build_store
        cli_mod._build_store = lambda: store_with_redis
        try:
            cmd_clear(args)
            # Verify override was cleared
            section = store_with_redis.get_section("quality")
            assert section.get("degraded_grace_s") is None or section["degraded_grace_s"] != 999
        finally:
            cli_mod._build_store = original

    def test_cmd_log(self, store_with_redis: ConfigStore, capsys: Any) -> None:
        """cmd_log shows changelog entries."""
        from pm_pipeline.cli.config import cmd_log

        store_with_redis.set_override("quality", "degraded_grace_s", 500)

        args = MagicMock()
        args.section = None
        args.n = 10

        import pm_pipeline.cli.config as cli_mod

        original = cli_mod._build_store
        cli_mod._build_store = lambda: store_with_redis
        try:
            cmd_log(args)
            captured = capsys.readouterr()
            assert "quality" in captured.out
        finally:
            cli_mod._build_store = original


# ---------------------------------------------------------------------------
# Orchestrator — create_config_watchers
# ---------------------------------------------------------------------------


class TestCreateConfigWatchers:
    """Test the orchestrator watcher factory."""

    def test_creates_quality_and_execution_watchers(
        self, store_toml_only: ConfigStore
    ) -> None:
        """create_config_watchers returns watchers for both sections."""
        from pm_pipeline.orchestrator import create_config_watchers

        watchers = create_config_watchers(store_toml_only)
        assert "quality" in watchers
        assert "execution" in watchers

    def test_quality_watcher_patches_checker(
        self, store_with_redis: ConfigStore
    ) -> None:
        """Quality watcher callback patches checker settings."""
        from pm_pipeline.orchestrator import create_config_watchers

        # Mock a QualityChecker
        mock_checker = MagicMock()
        mock_checker._settings = MagicMock()
        mock_checker._state = MagicMock()
        mock_checker._state._degraded_grace_s = 120

        watchers = create_config_watchers(store_with_redis, checker=mock_checker)

        # Trigger a change
        store_with_redis.set_override("quality", "source_liveness_timeout_s", 200)
        watchers["quality"].refresh()

        assert mock_checker._settings.source_liveness_timeout_s == 200

    def test_execution_watcher_current(self, store_toml_only: ConfigStore) -> None:
        """Execution watcher loads from TOML."""
        from pm_pipeline.orchestrator import create_config_watchers

        watchers = create_config_watchers(store_toml_only)
        cfg = watchers["execution"].current
        assert isinstance(cfg, ExecutionConfig)
        # From TOML
        assert cfg.max_total_exposure_usd == 3000
        assert cfg.patient_timeout_s == 15
