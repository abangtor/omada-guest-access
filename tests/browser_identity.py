"""Real Chromium persistence/automatic MAC transfer against the actual portal.

Run after installing Chromium: pytest -q tests/browser_identity.py
Controller I/O is mocked by conftest; portal, browser and HA storage are real.
"""

from datetime import timedelta
from urllib.parse import urlencode

from playwright.async_api import async_playwright

from custom_components.omada_guest_access.portal import GuestPortal
from tests.test_portal import QUERY


async def test_persistent_browser_reconnect_no_interaction(
    hass, coordinator, aiohttp_server, socket_enabled, freezer
):
    portal = GuestPortal(hass, coordinator, 0)
    server = await aiohttp_server(portal.create_app())
    coordinator.config["portal_url"] = str(server.make_url("/"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--no-sandbox"])
        try:
            browser_context = await browser.new_context()
            page = await browser_context.new_page()
            page.set_default_timeout(8000)
            await page.goto(str(server.make_url("/")) + "?" + urlencode(QUERY))
            await page.get_by_label("Your name").fill("Alex")
            await page.get_by_role("button", name="Request access", exact=True).click()
            await page.get_by_text("Request sent. Waiting for approval…", exact=True).wait_for()
            request_id = next(iter(coordinator.requests))
            approved = await coordinator.async_approve_request(request_id, 720)
            cookies = await browser_context.cookies()
            identity = next(cookie for cookie in cookies if cookie["name"] == portal._browser_cookie)
            assert identity["httpOnly"] and identity["sameSite"] == "Lax"
            assert identity["value"] not in await page.evaluate("document.cookie")
            assert identity["value"] not in await page.evaluate("JSON.stringify(localStorage)")
            saved = await browser_context.storage_state()
            await browser_context.close()
            portal._sessions.clear()
            freezer.tick(timedelta(days=7))
            coordinator.client.reset_mock()
            browser_context = await browser.new_context(storage_state=saved)
            page = await browser_context.new_page()
            page.set_default_timeout(8000)
            await page.goto(str(server.make_url("/")) + "?" + urlencode({**QUERY, "clientMac": "AA:BB:CC:DD:EE:02"}))
            # No form submission, consent click or host action on this return.
            await page.get_by_text("Access approved. You can now use the internet.", exact=True).wait_for()
            assert await page.locator("form").is_hidden()
            assert len(coordinator.requests) == 1
            assert coordinator.requests[request_id]["access_expires_at"] == approved["access_expires_at"]
            coordinator.client.async_revoke.assert_awaited_once()
            coordinator.client.async_authorize.assert_awaited_once()
            await browser_context.close()
        finally:
            await browser.close()
