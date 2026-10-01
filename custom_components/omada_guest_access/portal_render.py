"""Sandboxed, autoescaped portal layout templates with a fixed request UI contract."""

from __future__ import annotations

import hashlib
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
    CONF_PORTAL_MESSAGE,
    CONF_PORTAL_TEMPLATE,
    CONF_PORTAL_TITLE,
    CONF_REQUIRE_TERMS,
    CONF_SHOW_FORGET_BUTTON,
    CONF_SHOW_REMEMBER_CHECKBOX,
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
<body><main>
<h1>{{ title }}</h1><p>{{ message }}</p>
{{ form_html }}{{ status_html }}
</main>{{ script_html }}</body></html>"""


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
        # Empty compatibility aliases keep previously saved templates renderable.
        # Separate header/footer settings have been removed and are never rendered.
        "header_html": "",
        "footer_html": "",
        # Only integration-built fragments bypass escaping.
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
        memory_key=json.dumps("omada_guest_access.guest." + hashlib.sha256(
            json.dumps([config.get("controller_id", ""), config.get("site", "")]).encode()
        ).hexdigest()[:24]),
        form_hidden=" hidden" if request_id else "",
        nonce=escape(session_id, quote=True),
        title=escape(config.get(CONF_PORTAL_TITLE, DEFAULT_PORTAL_TITLE)),
        message=escape(config.get(CONF_PORTAL_MESSAGE, DEFAULT_PORTAL_MESSAGE)),
        accent=accent,
        terms=terms_html,
        remember_control="" if not config.get(CONF_SHOW_REMEMBER_CHECKBOX, True) else "<label class='consent'><input id='remember-details' type='checkbox' checked> Remember my name and note on this browser for 30 days.</label>",
        forget_control="" if not config.get(CONF_SHOW_FORGET_BUTTON, True) else "<button id='forget-details' type='button' hidden>Forget saved details</button>",
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
${remember_control}<button type='submit'>Request access</button>
${forget_control}</form><p id='status' role='status' aria-live='polite'></p><script id='portal-script' nonce='$nonce'>
const token=$token,f=document.querySelector('#request'),s=document.querySelector('#status');let id=$request_id,timer;
const memoryKey=$memory_key,memoryTTL=30*24*60*60*1000,remember=f.querySelector('#remember-details'),forget=f.querySelector('#forget-details');
function forgetDetails(){try{localStorage.removeItem(memoryKey)}catch(_){}if(forget)forget.hidden=true}
function restoreDetails(){try{
  const raw=localStorage.getItem(memoryKey);if(!raw)return;
  if(raw.length>8192)throw new Error();const saved=JSON.parse(raw),now=Date.now();
  if(!saved||saved.version!==1||typeof saved.guest_name!=='string'||!saved.guest_name.trim()||saved.guest_name.length>120||
    typeof saved.note!=='string'||saved.note.length>500||!Number.isFinite(saved.expires_at)||saved.expires_at<=now||saved.expires_at>now+memoryTTL)throw new Error();
  f.elements.guest_name.value=saved.guest_name;f.elements.note.value=saved.note;if(forget)forget.hidden=false;
}catch(_){forgetDetails()}}
function saveDetails(body){if(remember&&!remember.checked){forgetDetails();return}try{
  localStorage.setItem(memoryKey,JSON.stringify({version:1,guest_name:body.guest_name,note:body.note,expires_at:Date.now()+memoryTTL}));
  if(forget)forget.hidden=false;
}catch(_){/* Storage may be disabled by the captive browser; access still works. */}}
if(forget)forget.onclick=()=>{forgetDetails();f.elements.guest_name.value='';f.elements.note.value='';if(remember)remember.checked=false};
if(remember)remember.onchange=()=>{if(!remember.checked)forgetDetails()};
restoreDetails();
async function api(path,opts={}){let r=await fetch(path,{...opts,headers:{'Content-Type':'application/json','X-Portal-Token':token,...(opts.headers||{})}});if(!r.ok)throw new Error();return r.json()}
function showContinue(target){try{
  const url=new URL(target);if(!['http:','https:'].includes(url.protocol)||url.origin===location.origin||url.username||url.password)return;
  let link=document.querySelector('#continue-internet');if(!link){link=document.createElement('a');link.id='continue-internet';s.after(link)}
  link.href=url.href;link.rel='noreferrer';link.textContent='Continue to the internet';
}catch(_){}}
async function poll(){try{let x=await api('/api/request/'+id);if(x.status==='pending'){s.textContent='Request sent. Waiting for approval…';return;}clearInterval(timer);if(x.status==='approved'){s.textContent='Access approved. You can now use the internet.';if(x.redirect_url)showContinue(x.redirect_url)}else if(x.status==='denied')s.textContent=x.denial_reason||'Access was denied.';else if(x.status==='revoked')s.textContent='Your internet access has been cancelled.';else s.textContent='This request has expired.'}catch(_){clearInterval(timer);s.textContent='Your portal session expired. Please reconnect to Guest Wi-Fi.'}}
f.onsubmit=async e=>{e.preventDefault();const button=f.querySelector('button[type=submit]');button.disabled=true;
try{const body=Object.fromEntries(new FormData(f));if(f.elements.terms_accepted)body.terms_accepted=f.elements.terms_accepted.checked;
let x=await api('/api/request',{method:'POST',body:JSON.stringify(body)});id=x.request_id;saveDetails(body);f.hidden=true;s.textContent='Request sent. Waiting for approval…';timer=setInterval(poll,3000);await poll()}
catch(_){s.textContent='Unable to send your request. Please reconnect and try again.'}finally{button.disabled=false}};
if(id){f.hidden=true;s.textContent='Restoring your request…';timer=setInterval(poll,3000);poll();}
</script></body></html>"""
