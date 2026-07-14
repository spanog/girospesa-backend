from unittest.mock import MagicMock
from services.push_notify import notify_extraction_complete


def test_notify_extraction_creates_inbox_row_without_subscriptions():
    sb = MagicMock()
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


def test_notify_extraction_keeps_inbox_without_device_subscription():
    sb = MagicMock()
    push_resp = MagicMock()
    push_resp.data = []
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = push_resp
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock()

    notify_extraction_complete(sb, "flyer-1", "user-1", True, "Lidl", products_count=10)

    inserted_tables = [c.args[0] for c in sb.table.call_args_list]
    assert "app_notifications" in inserted_tables
