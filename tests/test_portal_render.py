"""Portal layout templates never receive HA objects or credentials."""

import pytest

from custom_components.omada_guest_access.portal_render import (
    DEFAULT_TEMPLATE,
    PortalTemplateError,
    _page,
    validate_template,
)
from custom_components.omada_guest_access.settings import parse_duration_options


def test_removed_header_footer_ignored_and_custom_css_preserved():
    page = _page(
        "token",
        {
            "portal_header": "<b>Welcome</b>",
            "portal_footer": "<p>Contact reception</p>",
            "portal_css": "body{background:#eeeeee}",
            "portal_title": "<script>bad()</script>",
        },
    )
    assert "<b>Welcome</b>" not in page
    assert "<p>Contact reception</p>" not in page
    assert "body{background:#eeeeee}" in page
    assert "&lt;script&gt;bad()&lt;/script&gt;" in page


def test_browser_memory_controls_can_be_hidden_without_disabling_prefill():
    page = _page(
        "token",
        {"show_remember_checkbox": False, "show_forget_button": False},
    )
    assert "id='remember-details'" not in page
    assert "id='forget-details'" not in page
    # With no opt-out control, successful submissions keep using local storage.
    assert "localStorage.setItem(memoryKey" in page
    assert "if(remember&&!remember.checked)" in page


def test_full_template_has_working_fragments_and_no_secrets():
    config = {
        "password": "never-disclose",
        "controller_url": "private-controller",
        "portal_title": "Welcome!",
        "portal_template": "<html><head>{{ style_html }}</head><body><aside>{{ title }}</aside>"
        "{{ form_html }}{{ status_html }}{{ script_html }}</body></html>",
    }
    page = _page("session-token", config, "request-id")
    assert "<aside>Welcome!</aside>" in page
    assert "id='request' hidden" in page
    assert "nonce='session-token'" in page
    assert "private-controller" not in page
    assert "never-disclose" not in page
    validate_template(config)


@pytest.mark.parametrize(
    "source",
    [
        "{{",
        "{{ states('sensor.secret') }}",
        "{{ config.password }}",
        "{{ ''.__class__.__mro__ }}",
        "{{ cycler.__init__.__globals__ }}",
        "<html>No form</html>",
        "{{ form_html }}{{ form_html }}{{ status_html }}{{ script_html }}",
        "{% if request_id %}missing{% else %}" + DEFAULT_TEMPLATE + "{% endif %}",
    ],
)
def test_invalid_templates_rejected(source):
    with pytest.raises(PortalTemplateError):
        validate_template({"portal_template": source})


def test_css_cannot_close_style_tag():
    page = _page("token", {"portal_css": "</style><script>alert(1)</script>"})
    assert "</style><script>alert" not in page
    assert "\\3c /style>" in page


@pytest.mark.parametrize("value", ["", "169", "1,8,200", "1.5", "1,,8", "True", [1, 8], "１"])
def test_invalid_duration_options(value):
    with pytest.raises(ValueError):
        parse_duration_options(value)


def test_duration_options_order_and_duplicates():
    assert parse_duration_options("168, 8, 1,8,24") == [1, 8, 24, 168]
    assert parse_duration_options("0, 8, 0") == [8, 0]


def test_legacy_template_aliases_render_empty():
    page = _page("token", {"portal_template": DEFAULT_TEMPLATE + "{{ header_html }}{{ footer_html }}",
                           "portal_header": "old header", "portal_footer": "old footer"})
    assert "old header" not in page and "old footer" not in page
