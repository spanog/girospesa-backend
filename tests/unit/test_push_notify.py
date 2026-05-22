from unittest.mock import MagicMock
from services.push_notify import notify_extraction_complete


def test_notify_extraction_creates_inbox_row_without_subscriptions():
    sb = MagicMock()
    # profile: notification_deals=True
    profile_resp = MagicMock()
    profile_resp.data = {"notification_deals": True}
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = profile_resp
    # push_subscriptions: empty
    push_resp = MagicMock()
    push_resp.data = []
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = push_resp
    # insert chain (captures the insert call)
    insert_resp = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = insert_resp

    notify_extraction_complete(sb, "flyer-1", "user-1", True, "Lidl", products_count=10)

    # Must have called sb.table("app_notifications")
    inserted_tables = [c.args[0] for c in sb.table.call_args_list]
    assert "app_notifications" in inserted_tables, f"Expected app_notifications insert, got: {inserted_tables}"

    # Verify the insert was called with correct payload
    insert_call_args = sb.table.return_value.insert.call_args[0][0]
    assert insert_call_args["user_id"] == "user-1"
    assert insert_call_args["kind"] == "extraction_complete"
    assert insert_call_args["data"]["flyer_id"] == "flyer-1"
    assert insert_call_args["data"]["status"] == "done"
    assert insert_call_args["data"]["products_count"] == 10


def test_notify_extraction_skips_inbox_when_deals_disabled():
    sb = MagicMock()
    profile_resp = MagicMock()
    profile_resp.data = {"notification_deals": False}
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = profile_resp

    notify_extraction_complete(sb, "flyer-1", "user-1", True, "Lidl", products_count=10)

    inserted_tables = [c.args[0] for c in sb.table.call_args_list]
    assert "app_notifications" not in inserted_tables, f"Should not insert when deals disabled, got: {inserted_tables}"
