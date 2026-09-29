"""Optional real-browser smoke test; no live HA/controller/browser traffic.

Install playwright and its Chromium browser first:
    .venv/bin/pip install playwright
    .venv/bin/playwright install chromium
    PYTHONPATH=. .venv/bin/python tests/browser_smoke.py
Or pass --executable /path/to/an/existing/chrome to reuse a local browser.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from custom_components.omada_guest_access.portal import _page


async def main(executable: str | None) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            card_path = (
                Path(__file__).parents[1] / "custom_components/omada_guest_access/frontend/omada-guest-access-card.js"
            )
            await page.goto("about:blank")
            await page.add_script_tag(content=card_path.read_text())
            await page.evaluate("""() => {
                window.calls=[];
                window.card=document.createElement('omada-guest-access-card');
                card.setConfig({pending_entity:'sensor.pending',active_entity:'sensor.active',duration_hours:4});
                window.mockHass={user:{is_admin:true}, states:{
                  'sensor.pending':{state:'1',attributes:{requests:[{request_id:'one',
                    guest_name:'<img src=x onerror=alert(1)>',note:'Visiting Alex',client_mac:'AA:BB:CC:DD:EE:FF',
                    expires_at:'2026-10-01T12:00:00Z'}]}},
                  'sensor.active':{state:'1',attributes:{sessions:[{request_id:'two',guest_name:'Sam',
                    client_mac:'11:22:33:44:55:66',access_expires_at:'2026-10-01T12:00:00Z'}],revoke_supported:false}}
                },callService:async (...args)=>{calls.push(args)}};
                card.hass=mockHass;document.body.append(card);
            }""")
            assert await page.locator("omada-guest-access-card img").count() == 0
            assert await page.get_by_text("<img src=x onerror=alert(1)>", exact=True).count() == 1
            await page.get_by_role("button", name="Approve 4h").click()
            assert await page.evaluate("calls[0]") == [
                "omada_guest_access",
                "approve_request",
                {"request_id": "one", "duration_hours": 4},
            ]
            assert await page.get_by_role("button", name="Revoke", exact=True).count() == 0
            await page.evaluate(
                "mockHass.callService=async()=>{throw new Error('Controller unavailable')};card.hass=mockHass"
            )
            await page.get_by_role("button", name="Deny", exact=True).click()
            await page.get_by_text("Controller unavailable", exact=True).wait_for()
            await page.evaluate("mockHass.user.is_admin=false;card.hass=mockHass")
            assert await page.get_by_role("button", name="Approve 4h").is_disabled()

            submissions = []

            async def route(request_route):
                path = request_route.request.url
                if path.endswith("/api/request"):
                    submissions.append(request_route.request.post_data_json)
                    assert request_route.request.headers["x-portal-token"] == "testtoken"
                    await request_route.fulfill(json={"request_id": "guest1", "status": "pending"})
                elif path.endswith("/api/request/guest1"):
                    await request_route.fulfill(json={"request_id": "guest1", "status": "approved"})
                else:
                    await request_route.fulfill(content_type="text/html", body=_page("testtoken"))

            await page.route("https://guest.example.com/**", route)
            await page.goto("https://guest.example.com/")
            await page.get_by_label("Your name").fill("Alex")
            await page.get_by_label("Optional note").fill("Visiting Sam")
            await page.get_by_role("button", name="Request access").click()
            await page.get_by_text("Access approved. You can now use the internet.", exact=True).wait_for()
            assert submissions == [{"guest_name": "Alex", "note": "Visiting Sam"}]
            assert not errors, errors
            print("Browser smoke passed: admin actions, errors, non-admin controls, XSS escaping, guest approval flow")
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable")
    asyncio.run(main(parser.parse_args().executable))
