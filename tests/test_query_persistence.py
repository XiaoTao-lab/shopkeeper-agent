from app.services.query_service import _resolve_committed_terminal


def test_persisted_interruption_replaces_a_late_success_result() -> None:
    persisted = {
        "status": "interrupted",
        "outcome": "error",
        "terminal_event": {
            "type": "error",
            "message": "查询超过执行期限，请重新提问。",
        },
    }
    late_result = {"type": "result", "data": [{"sales": "10.00"}]}

    event, status, outcome = _resolve_committed_terminal(
        persisted,
        late_result,
        "completed",
        "result",
    )

    assert event == persisted["terminal_event"]
    assert status == "interrupted"
    assert outcome == "error"
