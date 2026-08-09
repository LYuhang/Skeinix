from vibecanvas_api.agents.middleware.user_approval import requires_user_approval


def test_fetch_resource_requires_pre_tool_approval_by_default():
    assert requires_user_approval(
        "browser_fetch_resource",
        {"url": "https://example.test/private.pdf"},
        "agent",
    )


def test_fetch_resource_can_explicitly_lower_auth_requirement():
    assert not requires_user_approval(
        "browser_fetch_resource",
        {
            "url": "https://example.test/public-logo.png",
            "require_user_auth": False,
        },
        "agent",
    )


def test_always_ask_overrides_fetch_resource_lowered_requirement():
    assert requires_user_approval(
        "browser_fetch_resource",
        {
            "url": "https://example.test/public-logo.png",
            "require_user_auth": False,
        },
        "always_ask",
    )


def test_read_only_browser_snapshot_does_not_require_pre_tool_approval():
    assert not requires_user_approval(
        "browser_snapshot",
        {"tab": 42},
        "agent",
    )


def test_always_allow_is_only_a_pre_tool_policy_not_interactive_completion():
    # render_interactive is not a pre-execution authorization target. Its
    # wait/submit behavior is governed separately by completion_mode.
    assert not requires_user_approval(
        "render_interactive",
        {"completion_mode": "wait_for_submit"},
        "always_allow",
    )
