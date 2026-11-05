# -*- coding: utf-8 -*-
"""
tests/test_backup_manager.py — Unit tests for Enterprise Backup & Restore Manager (Gen 5.0).

Tests:
  1. JSON fallback backup creation, compression & integrity verification
  2. Backup listing with size, timestamp & sorting
  3. Retention policy pruning (prunes files older than N days)
  4. Archive verification (detects corrupt or empty files)
  5. DatabaseManager integration (create_backup, list_backups, backup_stats)
"""

from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.database import DatabaseManager
from scripts.backup_manager import (
    _create_json_backup,
    list_backups,
    prune_backups,
    verify_backup,
)


class TestBackupManager:

    def test_json_backup_creation_and_integrity(self, tmp_path: Path):
        """Test creating a compressed JSON backup archive and validating its structure."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Mock tables returning dummy data
        mock_cursor.fetchall.return_value = [
            {"guild_id": 12345, "volume": 1.0, "audio_quality": "high"}
        ]

        with patch("pymysql.connect", return_value=mock_conn):
            backup_file = _create_json_backup(
                target_dir=tmp_path,
                timestamp="20261105_120000",
                compress=True,
                h="127.0.0.1",
                p=3306,
                u="root",
                pwd="",
                db="musicbot",
            )

            assert backup_file.exists()
            assert backup_file.name.endswith(".json.gz")
            assert verify_backup(backup_file) is True

            # Decompress and verify content
            with gzip.open(backup_file, "rt", encoding="utf-8") as gz_f:
                data = json.load(gz_f)
                assert "server_configs" in data
                assert len(data["server_configs"]) == 1
                assert data["server_configs"][0]["guild_id"] == 12345

    def test_list_backups_ordering(self, tmp_path: Path):
        """Test list_backups returns archives sorted by modification time descending."""
        f1 = tmp_path / "backup_musicbot_20261101_100000.sql.gz"
        f2 = tmp_path / "backup_musicbot_20261102_100000.sql.gz"

        f1.write_bytes(b"dummy1")
        f2.write_bytes(b"dummy2")

        # Set distinct mtimes
        os.utime(f1, (time.time() - 200, time.time() - 200))
        os.utime(f2, (time.time() - 100, time.time() - 100))

        items = list_backups(dest_dir=tmp_path)
        assert len(items) == 2
        # f2 is newer -> first in list
        assert items[0]["filename"] == f2.name
        assert items[1]["filename"] == f1.name

    def test_prune_backups_retention(self, tmp_path: Path):
        """Test prune_backups deletes archives older than retention_days."""
        old_file = tmp_path / "backup_musicbot_old.sql.gz"
        new_file = tmp_path / "backup_musicbot_new.sql.gz"

        old_file.write_bytes(b"old")
        new_file.write_bytes(b"new")

        # Set old_file mtime to 10 days ago (10 * 86400)
        ten_days_ago = time.time() - (10 * 86400)
        os.utime(old_file, (ten_days_ago, ten_days_ago))

        # Run prune with 7 days retention
        pruned_count = prune_backups(dest_dir=tmp_path, retention_days=7)
        assert pruned_count == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_verify_backup_handles_corrupt_files(self, tmp_path: Path):
        """Test verify_backup returns False on empty or invalid gzip files."""
        empty_file = tmp_path / "empty.sql.gz"
        empty_file.write_bytes(b"")
        assert verify_backup(empty_file) is False

        corrupt_file = tmp_path / "corrupt.sql.gz"
        corrupt_file.write_bytes(b"not a valid gzip header")
        assert verify_backup(corrupt_file) is False

        valid_file = tmp_path / "valid.sql.gz"
        with gzip.open(valid_file, "wb") as gz:
            gz.write(b"SELECT 1;")
        assert verify_backup(valid_file) is True

    def test_database_manager_backup_delegation(self, tmp_path: Path):
        """Test DatabaseManager backup methods delegate cleanly."""
        db = DatabaseManager(":memory:")
        valid_file = tmp_path / "backup_musicbot_20261105.sql.gz"
        with gzip.open(valid_file, "wb") as gz:
            gz.write(b"SELECT 1;")

        with patch("scripts.backup_manager.list_backups", return_value=[{
            "filename": valid_file.name,
            "path": str(valid_file),
            "size_mb": 1.25,
            "created_at": "2026-11-05T12:00:00Z",
            "mtime": time.time(),
        }]):
            stats = db.backup_stats()
            assert stats["total_backups"] == 1
            assert stats["latest_backup"] == valid_file.name
            assert stats["latest_backup_mb"] == 1.25

