from vibecanvas_api.storage.repo_deployment_invocations import _operational_error


def test_invocation_history_keeps_only_stable_error_codes():
    assert _operational_error("service_account_unavailable") == (
        "service_account_unavailable"
    )
    assert _operational_error("enqueue_failed:ConnectionError") == (
        "enqueue_failed:connectionerror"
    )
    assert _operational_error("customer secret appeared in traceback") == (
        "execution_failed"
    )
