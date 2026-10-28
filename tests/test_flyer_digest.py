from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from domain.flyer_digest import DigestConfigurationError, FlyerDigest, digest_opens
from services.repositories.flyer_digest_repository import FlyerDigestRepository, atomic


def test_digest_groups_branch_names_without_summing_duplicate_offer_counts():
    digest = FlyerDigest("u", "2026-10-28", tuple(
        {"flyer_id": f"f{i}", "supermarket_id": f"s{i}", "supermarket_name": name}
        for i, name in enumerate(["Conad", "CONAD", "Lidl", "Coop", "Penny"])
    ))
    assert digest.body() == "Novità da CONAD, Coop, Lidl e un altro supermercato."
    assert digest.data()["flyer_ids"] == [f"f{i}" for i in range(5)]
    assert digest.data()["url"] == "/volantini"
    assert "products_count" not in digest.data()
    assert digest.key == "flyer-digest:u:2026-10-28"


def test_daily_boundary_uses_rome_across_dst_and_midnight():
    winter = datetime(2026, 10, 28, 23, 30, tzinfo=UTC)
    assert digest_opens(winter) == datetime(2026, 10, 29, 9, tzinfo=UTC)
    summer = datetime(2026, 10, 24, 12, tzinfo=UTC)
    assert digest_opens(summer) == datetime(2026, 10, 24, 8, tzinfo=UTC)
    assert digest_opens(summer, tomorrow=True) == datetime(2026, 10, 25, 9, tzinfo=UTC)


def test_transaction_rolls_back_and_propagates_failure():
    cursor = MagicMock()
    with pytest.raises(ValueError), atomic(cursor):
        raise ValueError("cannot build digest")
    assert [c.args[0] for c in cursor.execute.call_args_list] == ["BEGIN", "ROLLBACK"]


def test_without_database_worker_fails_closed():
    with patch("services.repositories.flyer_digest_repository.has_direct_postgres", return_value=False):
        with pytest.raises(DigestConfigurationError), FlyerDigestRepository().cycle():
            pytest.fail("unsafe fallback")


def test_already_attempted_delivery_never_recreates_inbox():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"payload": {"delivery_started_at": "2026-10-28T10:00:00Z"}}
    repo = FlyerDigestRepository()
    assert repo.reserve_delivery(cursor, {"id": "job"}, datetime.now(UTC)) is None
    assert not any("INSERT" in call.args[0] for call in cursor.execute.call_args_list)


def test_prepare_does_nothing_before_ten():
    cursor = MagicMock()
    FlyerDigestRepository().prepare(cursor, datetime(2026, 10, 28, 8, 59, tzinfo=UTC))
    cursor.execute.assert_not_called()


def test_push_payload_stays_small_for_large_admin_digests():
    from services.push_notify import _digest_push_data, deliver_flyer_digest
    data = {"kind": "flyer_published", "aggregation_key": "daily-key", "url": "/volantini",
            "flyer_ids": [str(i) * 36 for i in range(200)], "supermarket_ids": []}
    delivery = {"enabled": False, "user_id": "u", "title": "Nuovi volantini", "body": "Conad", "data": data}
    assert _digest_push_data(delivery) == {key: data[key] for key in ("kind", "aggregation_key", "url")}
    with patch("services.push_notify._send_push_to_user") as send:
        deliver_flyer_digest(MagicMock(), delivery, MagicMock())
    send.assert_not_called()
    assert len(data["flyer_ids"]) == 200
