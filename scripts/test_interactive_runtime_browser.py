from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    requested = []
    console_messages = []
    page.on("console", lambda message: console_messages.append(message.text))
    page.on("pageerror", lambda error: console_messages.append(f"PAGEERROR: {error}"))

    def serve_resource(route):
        requested.append(route.request.url)
        if route.request.url.endswith("manifest.json"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '[{"id":"sample-1","path":"/data/dataset/1.png"},'
                    '{"id":"sample-2","path":"/mount/uploads/2.png"}]'
                ),
                headers={"Access-Control-Allow-Origin": "*"},
            )
        else:
            route.fulfill(
                status=200,
                content_type="image/png",
                body=b"\x89PNG\r\n\x1a\n",
                headers={"Access-Control-Allow-Origin": "*"},
            )

    page.route("**/resources/**", serve_resource)
    page.goto("http://127.0.0.1:4178/tests/interactive-runtime-harness.html")
    page.wait_for_load_state("networkidle")
    frame = page.frame_locator("#artifact-frame")
    label = frame.locator('input[name="sample-1.label"]')
    label.wait_for()
    assert label.input_value() == "restored"
    assert not page.evaluate(
        "window.interactiveMessages.some(m => m && m.type === 'submit')"
    )
    label.fill("pass")
    frame.locator('textarea[name="sample-1.reason"]').fill("clear image")
    frame.locator('input[name="sample-2.label"]').fill("reject")
    frame.locator('textarea[name="sample-2.reason"]').fill("blurred")
    frame.get_by_role("button", name="Submit annotations").click()
    page.wait_for_timeout(1000)
    page.wait_for_function(
        "window.interactiveMessages.some(m => m && m.type === 'submit')"
    )
    submitted = page.evaluate(
        "window.interactiveMessages.findLast(m => m && m.type === 'submit')"
    )
    assert submitted["state"]["fields"] == {
        "sample-1.label": "pass",
        "sample-1.reason": "clear image",
        "sample-2.label": "reject",
        "sample-2.reason": "blurred",
    }
    assert frame.locator("body").get_attribute("data-isolated") == "yes"
    assert page.locator("body").get_attribute("data-compromised") is None
    assert any(url.endswith("/resources/opaque/data/dataset/manifest.json") for url in requested)
    assert any(url.endswith("/resources/opaque/data/dataset/1.png") for url in requested)
    assert any(url.endswith("/resources/mount-opaque/mount/uploads/2.png") for url in requested)
    assert not [message for message in console_messages if message.startswith("PAGEERROR:")]
    browser.close()
