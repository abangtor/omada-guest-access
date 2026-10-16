"""Exercise configurable guest text, blank suppression and untrusted API errors."""

from playwright.async_api import expect

from custom_components.omada_guest_access.portal_render import _page


async def check_guest_messages(browser):
    page = await browser.new_page()
    settings = {}
    status = "denied"
    failure = False
    resumed = True
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def route(request_route):
        path = request_route.request.url
        if "/api/" in path:
            if failure:
                await request_route.fulfill(status=500, body="SECRET backend exception")
            else:
                await request_route.fulfill(json={"status": status, "denial_reason": "SECRET internal denial"})
        else:
            await request_route.fulfill(
                content_type="text/html", body=_page("testtoken", settings, "guest1" if resumed else None)
            )

    try:
        await page.route("https://messages.example/**", route)
        for state in ("denied", "expired", "revoked"):
            status = state
            for message in ('Contact <reception> </script><script>bad()</script>', ""):
                settings[f"portal_{state}_message"] = message
                await page.goto("https://messages.example/")
                await expect(page.locator("#status")).to_have_text(message)
                assert "SECRET" not in await page.locator("body").inner_text()
        failure = True
        for message in ("Please reconnect.", ""):
            settings["portal_session_error_message"] = message
            await page.goto("https://messages.example/")
            await expect(page.locator("#status")).to_have_text(message)
        resumed = False
        for message in ("Contact your host.", ""):
            settings["portal_submit_error_message"] = message
            await page.goto("https://messages.example/")
            await page.get_by_label("Your name").fill("Alex")
            await page.get_by_role("button", name="Request access").click()
            await expect(page.get_by_role("button", name="Request access")).to_be_enabled()
            await expect(page.locator("#status")).to_have_text(message)
            assert "SECRET" not in await page.locator("body").inner_text()
        assert not errors, errors
    finally:
        await page.close()
