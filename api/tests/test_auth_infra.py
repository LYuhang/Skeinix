from vibecanvas_api.auth.email_sender import DevEmailSender
from vibecanvas_api.auth.ratelimit import LoginRateLimiter


def test_dev_email_sender_writes_stderr(capsys):
    DevEmailSender().send("u@example.com", "subject", "reset-token-xyz")
    err = capsys.readouterr().err
    assert "u@example.com" in err and "reset-token-xyz" in err


def test_rate_limiter_blocks_after_threshold():
    rl = LoginRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        assert rl.check("k") is True
        rl.record_failure("k")
    assert rl.check("k") is False        # 4th attempt blocked


def test_rate_limiter_success_resets():
    rl = LoginRateLimiter(max_attempts=3, window_seconds=60)
    rl.record_failure("k"); rl.record_failure("k")
    rl.record_success("k")
    assert rl.check("k") is True


def test_rate_limiter_window_expiry():
    """Failures older than the window are pruned on the next check."""
    rl = LoginRateLimiter(max_attempts=1, window_seconds=0)
    rl.record_failure("k"); rl.record_failure("k")
    assert rl.check("k") is True         # both failures fall outside the 0s window
