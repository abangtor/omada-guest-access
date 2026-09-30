"""Sandboxed, autoescaped portal layout templates with a fixed request UI contract."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from html import escape
from string import Template
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from markupsafe import Markup

from .const import (
    CONF_PORTAL_ACCENT,
    CONF_PORTAL_CSS,
    CONF_PORTAL_FOOTER,
    CONF_PORTAL_HEADER,
    CONF_PORTAL_MESSAGE,
    CONF_PORTAL_TEMPLATE,
    CONF_PORTAL_TITLE,
    CONF_REQUIRE_TERMS,
    CONF_TERMS_TEXT,
    DEFAULT_PORTAL_ACCENT,
    DEFAULT_PORTAL_MESSAGE,
    DEFAULT_PORTAL_TITLE,
)


class PortalTemplateError(ValueError):
    """Invalid or unsafe template; never include rendered guest/session data."""


_ENV = ImmutableSandboxedEnvironment(autoescape=True, undefined=StrictUndefined)
_ENV.globals.clear()  # No HA objects, filesystem loader, credentials, or callable globals.

DEFAULT_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>{{ style_html }}</head>
<body><header>{{ header_html }}</header><main>
<h1>{{ title }}</h1><p>{{ message }}</p>
{{ form_html }}{{ status_html }}
</main><footer>{{ footer_html }}</footer>{{ script_html }}</body></html>"""


@lru_cache(maxsize=16)
def _compile(source: str):
    return _ENV.from_string(source)


def _page(session_id: str, config: dict[str, Any] | None = None, request_id: str | None = None) -> str:
    config = config or {}
    builtin = _builtin_page(session_id, config, request_id)
    style = builtin[builtin.index("<style>") : builtin.index("</style>") + 8]
    form = builtin[builtin.index("<form ") : builtin.index("</form>") + 7]
    status = "<p id='status' role='status' aria-live='polite'></p>"
    script = builtin[builtin.index("<script ") : builtin.index("</script>") + 9]
    # Prevent CSS from breaking out of its style element. External assets remain CSP-blocked.
    css = config.get(CONF_PORTAL_CSS, "").replace("<", "\\3c ")
    style += "<style>" + css + "</style>"
    context = {
        "title": config.get(CONF_PORTAL_TITLE, DEFAULT_PORTAL_TITLE),
        "message": config.get(CONF_PORTAL_MESSAGE, DEFAULT_PORTAL_MESSAGE),
        "accent": config.get(CONF_PORTAL_ACCENT, DEFAULT_PORTAL_ACCENT),
        "terms_text": config.get(CONF_TERMS_TEXT, ""),
        "require_terms": config.get(CONF_REQUIRE_TERMS, False),
        "request_id": request_id,
        # Only admin-authored HTML and integration-built fragments bypass escaping.
        "header_html": Markup(config.get(CONF_PORTAL_HEADER, "")),
        "footer_html": Markup(config.get(CONF_PORTAL_FOOTER, "")),
        "style_html": Markup(style),
        "form_html": Markup(form),
        "status_html": Markup(status),
        "script_html": Markup(script),
    }
    source = config.get(CONF_PORTAL_TEMPLATE, "").strip() or DEFAULT_TEMPLATE
    try:
        result = _compile(source).render(context)
    except (TemplateError, TypeError, ValueError, OverflowError, RecursionError) as err:
        raise PortalTemplateError(f"Invalid portal Jinja template ({type(err).__name__})") from err
    if len(result) > 256000:
        raise PortalTemplateError("Rendered portal exceeds 256000 characters")
    # Whole fragments must appear once so customization cannot accidentally drop
    # name/consent controls, status recovery, or the session-bound API script.
    if any(result.count(fragment) != 1 for fragment in (form, status, script)):
        raise PortalTemplateError("Include form_html, status_html and script_html exactly once")
    return result


def validate_template(config: dict[str, Any]) -> None:
    """Validate both initial and resumed layouts when options are saved."""
    _page("validation-token", config)
    _page("validation-token", config, "validation-request")


def _builtin_page(session_id: str, config: dict[str, Any] | None = None, request_id: str | None = None) -> str:
    config = config or {}
    terms = config.get(CONF_TERMS_TEXT, "").strip()
    terms_html = ""
    if terms:
        terms_html = (
            "<details id='terms' open><summary>Guest Wi-Fi terms</summary><p>" + escape(terms) + "</p></details>"
        )
    if config.get(CONF_REQUIRE_TERMS):
        terms_html += "<label class='consent'><input type='checkbox' name='terms_accepted' required> I agree to the guest Wi-Fi terms.</label>"
    accent = config.get(CONF_PORTAL_ACCENT, DEFAULT_PORTAL_ACCENT)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = DEFAULT_PORTAL_ACCENT
    return Template(_PAGE).substitute(
        token=json.dumps(session_id),
        request_id=json.dumps(request_id),
        form_hidden=" hidden" if request_id else "",
        nonce=escape(session_id, quote=True),
        title=escape(config.get(CONF_PORTAL_TITLE, DEFAULT_PORTAL_TITLE)),
        message=escape(config.get(CONF_PORTAL_MESSAGE, DEFAULT_PORTAL_MESSAGE)),
        accent=accent,
        terms=terms_html,
    )


_PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>$title</title><style>
body{font-family:system-ui;max-width:440px;margin:8vh auto;padding:1rem;color:#15202b;background:#fff}
input,textarea,button{box-sizing:border-box;width:100%;padding:.8rem;margin:.4rem 0}
textarea{min-height:6rem;resize:vertical;font:inherit}
button{background:$accent;color:#fff;border:0;border-radius:.3rem;font:inherit;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}input[type=checkbox]{width:auto;margin-right:.5rem}
#status{margin-top:1rem}#terms{margin:1rem 0}p{white-space:pre-wrap;overflow-wrap:anywhere}
.consent{display:block;margin:1rem 0}small{color:#52606d}
</style></head><body>
<h1>$title</h1><p>$message</p><form id='request'$form_hidden>
<label>Your name<input name='guest_name' required maxlength='120' autocomplete='name'></label>
<label>Optional note<textarea name='note' maxlength='500' rows='4'></textarea></label>$terms
<button>Request access</button></form><p id='status' role='status' aria-live='polite'></p><script id='portal-script' nonce='$nonce'>
const token=$token,f=document.querySelector('#request'),s=document.querySelector('#status');let id=$request_id,timer;
async function api(path,opts={}){let r=await fetch(path,{...opts,headers:{'Content-Type':'application/json','X-Portal-Token':token,...(opts.headers||{})}});if(!r.ok)throw new Error();return r.json()}
async function poll(){try{let x=await api('/api/request/'+id);if(x.status==='pending'){s.textContent='Request sent. Waiting for approval…';return;}clearInterval(timer);if(x.status==='approved'){s.textContent='Access approved. You can now use the internet.';if(x.redirect_url)location.assign(x.redirect_url)}else if(x.status==='denied')s.textContent=x.denial_reason||'Access was denied.';else if(x.status==='revoked')s.textContent='Your internet access has been cancelled.';else s.textContent='This request has expired.'}catch(_){clearInterval(timer);s.textContent='Your portal session expired. Please reconnect to Guest Wi-Fi.'}}
f.onsubmit=async e=>{e.preventDefault();const button=f.querySelector('button');button.disabled=true;
try{const body=Object.fromEntries(new FormData(f));if(f.elements.terms_accepted)body.terms_accepted=f.elements.terms_accepted.checked;
let x=await api('/api/request',{method:'POST',body:JSON.stringify(body)});id=x.request_id;f.hidden=true;s.textContent='Request sent. Waiting for approval…';timer=setInterval(poll,3000);await poll()}
catch(_){s.textContent='Unable to send your request. Please reconnect and try again.'}finally{button.disabled=false}};
if(id){f.hidden=true;s.textContent='Restoring your request…';timer=setInterval(poll,3000);poll();}
</script></body></html>"""
