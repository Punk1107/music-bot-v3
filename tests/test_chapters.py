# -*- coding: utf-8 -*-
"""
tests/test_chapters.py — Unit test suite for chapters cog & timestamp parser (Feature 1.2).
"""

from __future__ import annotations

import pytest
from chapters.cog import ChaptersCog, _parse_timestamp


class TestChaptersCog:
    def test_parse_timestamp(self):
        # Seconds
        assert _parse_timestamp("90") == 90.0
        assert _parse_timestamp("0") == 0.0

        # MM:SS
        assert _parse_timestamp("1:30") == 90.0
        assert _parse_timestamp("03:45") == 225.0

        # HH:MM:SS
        assert _parse_timestamp("1:00:00") == 3600.0
        assert _parse_timestamp("01:23:45") == 5025.0

        # Invalid strings
        assert _parse_timestamp("invalid") is None
        assert _parse_timestamp("") is None
