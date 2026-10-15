from app.repositories.mysql.meta.history_repository import HistoryError


def test_request_in_progress_can_include_the_run_status_in_error_details() -> None:
    error = HistoryError(
        409,
        "REQUEST_IN_PROGRESS",
        run_id="run-123",
        status="running",
    )

    assert error.status == 409
    assert error.details == {"run_id": "run-123", "status": "running"}
