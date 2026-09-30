"""Returning guests keep editable hints, never authorization or consent."""

from custom_components.omada_guest_access.portal_render import _page


async def check_guest_memory(browser):
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    config = {"controller_id": "one", "site": "garden", "require_terms": True, "terms_text": "Be kind"}
    submissions = []
    fail = False

    async def route(request):
        if request.request.url.endswith("/api/request"):
            if fail:
                await request.fulfill(status=503)
                return
            submissions.append(request.request.post_data_json)
            await request.fulfill(json={"request_id": "one"})
        elif "/api/request/" in request.request.url:
            await request.fulfill(json={"status": "pending"})
        else:
            # Every navigation represents a NEW portal session, including after HA restart.
            await request.fulfill(content_type="text/html", body=_page("session", config))

    await context.route("https://returning.example/**", route)
    await page.goto("https://returning.example/")
    assert await page.get_by_label("Your name").input_value() == ""
    await page.get_by_label("Your name").fill("Alex <b>guest</b>")
    await page.get_by_label("Optional note").fill("Visit Sam\nRoom 42")
    await page.get_by_label("I agree to the guest Wi-Fi terms.").check()
    await page.get_by_role("button", name="Request access", exact=True).click()
    await page.get_by_text("Request sent. Waiting for approval…", exact=True).wait_for()
    saved = await page.evaluate("JSON.parse(localStorage.getItem(localStorage.key(0)))")
    assert set(saved) == {"version", "guest_name", "note", "expires_at"}
    # New page, same persistent browser storage. No API call and no consent recovery.
    await page.close()
    page = await context.new_page()
    page.set_default_timeout(5000)
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.goto("https://returning.example/")
    assert await page.get_by_label("Your name").input_value() == "Alex <b>guest</b>"
    assert await page.get_by_label("Optional note").input_value() == "Visit Sam\nRoom 42"
    assert not await page.get_by_label("I agree to the guest Wi-Fi terms.").is_checked()
    assert await page.locator("form").is_visible()
    assert len(submissions) == 1
    assert await page.locator("input[name=guest_name] b").count() == 0

    # Editing prefilled details replaces the saved values only on successful submission.
    await page.get_by_label("Your name").fill("Jamie")
    await page.get_by_label("Optional note").fill("")
    await page.get_by_label("I agree to the guest Wi-Fi terms.").check()
    fail = True
    await page.get_by_role("button", name="Request access", exact=True).click()
    await page.get_by_text("Unable to send your request. Please reconnect and try again.").wait_for()
    await page.reload()
    assert await page.get_by_label("Your name").input_value() == "Alex <b>guest</b>"
    fail = False
    await page.get_by_label("Your name").fill("Jamie")
    await page.get_by_label("Optional note").fill("")
    await page.get_by_label("I agree to the guest Wi-Fi terms.").check()
    await page.get_by_role("button", name="Request access", exact=True).click()
    await page.get_by_text("Request sent. Waiting for approval…", exact=True).wait_for()
    await page.reload()
    assert await page.get_by_label("Your name").input_value() == "Jamie"
    assert await page.get_by_label("Optional note").input_value() == ""

    # Shared portal hostname: one site's details never prefill another site/controller.
    for field in ("site", "controller_id"):
        original = config[field]
        config[field] = "different"
        await page.reload()
        assert await page.get_by_label("Your name").input_value() == ""
        config[field] = original
    await page.reload()
    assert await page.get_by_label("Your name").input_value() == "Jamie"
    await page.get_by_role("button", name="Forget saved details").click()
    assert await page.get_by_label("Your name").input_value() == ""
    assert await page.get_by_label("Optional note").input_value() == ""
    assert await page.evaluate("localStorage.length") == 0
    # Opting out still allows a new request and does not save anything.
    await page.get_by_label("Your name").fill("Not remembered")
    await page.get_by_label("I agree to the guest Wi-Fi terms.").check()
    await page.get_by_role("button", name="Request access", exact=True).click()
    await page.get_by_text("Request sent. Waiting for approval…", exact=True).wait_for()
    assert await page.evaluate("localStorage.length") == 0
    await page.reload()
    assert await page.get_by_label("Your name").input_value() == ""

    # Expired, invalid and oversized data is discarded, without breaking the form.
    key = await page.evaluate("memoryKey")
    for raw in ("{", "null", "x" * 8193):
        await page.evaluate("([key, raw]) => localStorage.setItem(key, raw)", [key, raw])
        await page.reload()
        assert await page.get_by_label("Your name").input_value() == ""
        assert await page.evaluate("localStorage.length") == 0
    for change in ({"expires_at": 0}, {"expires_at": 999999999999999}, {"guest_name": ["bad"]}, {"note": "x" * 501}):
        await page.evaluate(
            "([key, saved, change]) => localStorage.setItem(key, JSON.stringify({...saved,...change}))",
            [key, saved, change],
        )
        await page.reload()
        assert await page.get_by_label("Your name").input_value() == ""
        assert await page.evaluate("localStorage.length") == 0

    # Storage disabled in a captive browser must not prevent normal access requests.
    await page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new Error('disabled')}})")
    await page.reload()
    await page.get_by_label("Your name").fill("No storage")
    await page.get_by_label("I agree to the guest Wi-Fi terms.").check()
    await page.get_by_role("button", name="Request access", exact=True).click()
    await page.get_by_text("Request sent. Waiting for approval…", exact=True).wait_for()
    assert submissions[-1] == {"guest_name": "No storage", "note": "", "terms_accepted": True}
    assert not errors, errors
    await context.close()
