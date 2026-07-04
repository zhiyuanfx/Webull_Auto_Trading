from webull_bridge.cli import summarize_accounts


def test_summarize_accounts_extracts_candidate_ids() -> None:
    payload = {
        "data": [
            {
                "accountId": "acct-1",
                "accountType": "MARGIN",
                "status": "ACTIVE",
                "accessToken": "secret-token",
            },
            {"account_id": "acct-2", "currency": "USD", "nested": {"accountId": "acct-2"}},
        ]
    }

    result = summarize_accounts(payload)

    assert result["account_id_candidates"] == ["acct-1", "acct-2"]
    assert result["copy_this_to_route_account_id"] is None
    assert result["account_count"] == 2
    assert result["accounts"][0] == {
        "account_id": "acct-1",
        "accountType": "MARGIN",
        "status": "ACTIVE",
    }


def test_summarize_accounts_marks_single_id_to_copy() -> None:
    result = summarize_accounts({"account_id": "acct-1"})

    assert result["copy_this_to_route_account_id"] == "acct-1"
