"""Unit tests for nearby-supermarket discovery helpers."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.routers._nearby_supermarkets import _active_offer_supermarket_ids


def test_active_offer_supermarkets_use_single_database_exists_lookup():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value.data = [{"id": "store-1"}, {"id": "store-2"}]

    result = _active_offer_supermarket_ids(sb, ["store-1", "store-2", "store-3"])

    assert result == ["store-1", "store-2"]
    sb.rpc.assert_called_once_with(
        "current_public_offer_supermarket_ids",
        {"candidate_supermarket_ids": ["store-1", "store-2", "store-3"]},
    )
    sb.table.assert_not_called()
