"""Card editor contract and stateful rendering regressions, called by browser_smoke."""


async def check_card(page):
    """Exercise configuration, disclosure state, and isolation in real Chromium."""
    await page.evaluate("""() => {
      window.baseConfig = {type:'custom:omada-guest-access-card', pending_entity:'sensor.pending',
        active_entity:'sensor.active',duration_hours:4};
      mockHass.user = {id:'admin-one',is_admin:true};
      mockHass.states['sensor.pending'].entity_id = 'sensor.pending';
      mockHass.states['sensor.active'].entity_id = 'sensor.active';
      mockHass.states['sensor.active'].attributes.entry_id = 'entry-one';
      mockHass.callWS = async msg => {
        window.historyCalls.push(msg);
        return {total:55, offset:msg.offset, limit:msg.limit, requests:[{
          request_id:'record-'+msg.offset,guest_name:'History guest',status:'denied',
          client_mac:'AA:BB:CC:DD:EE:02',note:'Historical note',created_at:'2026-09-29T12:00:00Z',
          updated_at:'2026-09-29T13:00:00Z',decision_user_id:'decision-admin',
          terms_accepted_at:'2026-09-29T12:00:00Z',terms_text:'Archived terms',terms_version:'hash'
        }]};
      };
      card.setConfig(baseConfig);card.hass=mockHass;
    }""")
    await check_name_labels(page)
    await page.get_by_role("button", name="Show history", exact=True).click()
    details = page.locator("omada-guest-access-card details")
    await page.get_by_text("Request details", exact=True).click()
    assert await details.get_attribute("open") is not None
    # Updates from unrelated entities must leave the DOM (and selection/focus) alone.
    await page.evaluate("""() => {
      window.originalDetails=card.shadowRoot.querySelector('details');
      mockHass.states['sensor.unrelated']={state:'new'};card.hass=mockHass;
    }""")
    assert await page.evaluate("originalDetails===card.shadowRoot.querySelector('details')")
    # A relevant state update may redraw, but must retain expanded and collapsed states.
    await page.evaluate("mockHass.states['sensor.pending'].last_updated='changed';card.hass=mockHass")
    assert await details.get_attribute("open") is not None
    await page.get_by_text("Request details", exact=True).click()
    await page.evaluate("mockHass.states['sensor.active'].last_updated='changed';card.hass=mockHass")
    assert await details.get_attribute("open") is None
    await page.get_by_text("Request details", exact=True).click()
    await page.get_by_role("button", name="Search / refresh", exact=True).click()
    await page.get_by_text("Archived terms", exact=True).wait_for()
    assert await details.get_attribute("open") is not None
    # Navigate away and back: expansion is keyed to request ID, not row position.
    await page.get_by_role("button", name="Next", exact=True).click()
    assert await details.get_attribute("open") is None
    await page.get_by_role("button", name="Previous", exact=True).click()
    assert await details.get_attribute("open") is not None
    search = page.get_by_label("Search history", exact=True)
    await search.fill("Alex")
    await page.evaluate("mockHass.states['sensor.pending'].last_updated='again';card.hass=mockHass")
    assert await search.input_value() == "Alex"
    assert await search.evaluate("el => el.getRootNode().activeElement === el && el.selectionStart === 4")

    # Each configurable section can be hidden independently, including all sections.
    for key, heading in [
        ("show_pending", "Pending (1)"),
        ("show_active", "Unexpired grants (1)"),
        ("show_history", "Request history"),
    ]:
        await page.evaluate("key => card.setConfig({...baseConfig,[key]:false})", key)
        assert await page.get_by_role("heading", name=heading, exact=True).count() == 0
    await page.evaluate("""() => {
      window.callsBefore=historyCalls.length;
      card.setConfig({...baseConfig,show_title:false,show_pending:false,show_active:false,show_history:false});
      card.hass=mockHass;
    }""")
    assert await page.locator("omada-guest-access-card h2, omada-guest-access-card h3").count() == 0
    assert await page.evaluate("historyCalls.length===callsBefore")

    await page.evaluate("""card.setConfig({...baseConfig,show_notes:false,show_mac:false,show_timestamps:false,
      show_actions:false,show_notices:false,show_decision_user:false,show_terms:false,compact:true,history_page_size:5})""")
    assert await page.get_by_role("button", name="Approve", exact=True).count() == 0
    assert await page.get_by_role("button", name="Deny", exact=True).count() == 0
    assert await page.get_by_text("Visiting Alex", exact=True).count() == 0
    assert await page.get_by_text("Local grant records; not a live controller client list.", exact=True).count() == 0
    assert (
        await page.locator("omada-guest-access-card ha-card").evaluate("el => getComputedStyle(el).padding") == "12px"
    )
    await page.get_by_role("button", name="Show history", exact=True).click()
    await page.get_by_text("Request details", exact=True).click()
    assert await page.evaluate("historyCalls.at(-1).limit") == 5
    for hidden in ["Historical note", "AA:BB:CC:DD:EE:02", "Last updated:", "decision-admin", "Archived terms"]:
        assert hidden not in await page.locator("omada-guest-access-card ha-card").inner_text()
    await page.get_by_role("button", name="Next", exact=True).click()
    assert await page.evaluate("historyCalls.at(-1).offset") == 5
    await page.evaluate("card.setConfig({...baseConfig,show_history_details:false})")
    await page.get_by_role("button", name="Show history", exact=True).click()
    assert await details.count() == 0
    await page.evaluate("""() => {
      window.savedRequests=mockHass.states['sensor.pending'].attributes.requests;
      window.savedSessions=mockHass.states['sensor.active'].attributes.sessions;
      mockHass.states['sensor.pending'].attributes.requests=[];
      mockHass.states['sensor.active'].attributes.sessions=[];
      card.setConfig({...baseConfig,show_empty:false});card.hass=mockHass;
    }""")
    assert await page.get_by_role("heading", name="Pending (0)", exact=True).count() == 0
    assert await page.get_by_role("heading", name="Unexpired grants (0)", exact=True).count() == 0
    await page.evaluate("""() => {
      mockHass.states['sensor.pending'].attributes.requests=savedRequests;
      mockHass.states['sensor.active'].attributes.sessions=savedSessions;
      card.setConfig(baseConfig);card.hass=mockHass;
    }""")

    # Security boundary: old history and expansion never cross identities or entries.
    await page.get_by_role("button", name="Show history", exact=True).click()
    await page.get_by_text("Request details", exact=True).click()
    await page.evaluate("mockHass.user.id='admin-two';card.hass=mockHass")
    assert await details.count() == 0
    await page.get_by_role("button", name="Show history", exact=True).click()
    assert await details.get_attribute("open") is None
    await page.evaluate("() => { mockHass.callWS=()=>new Promise(resolve=>{window.resolveHiddenHistory=resolve}); }")
    await page.get_by_role("button", name="Search / refresh", exact=True).click()
    await page.evaluate("""() => {
      card.setConfig({...baseConfig,show_history:false});
      resolveHiddenHistory({total:1,offset:0,limit:20,requests:[{guest_name:'Hidden late history'}]});
    }""")
    assert await page.get_by_text("Hidden late history", exact=True).count() == 0

    # Exercise the standard HA ha-form schema/config-changed contract without substituting
    # a fake HA UI. HA supplies the actual selector controls at runtime.
    result = await page.evaluate("""async () => {
      const cls=customElements.get('omada-guest-access-card');
      const stub=cls.getStubConfig(mockHass);
      const editor=await cls.getConfigElement();
      window.editor=editor;document.body.append(editor);
      const input={...baseConfig,title:'My guests',grid_options:{columns:6}};
      editor.setConfig(input);editor.hass=mockHass;
      const form=editor.shadowRoot.querySelector('ha-form');
      const initial={...form.data};
      window.editorEvents=[];
      editor.addEventListener('config-changed', event=>editorEvents.push({
        config:event.detail.config,bubbles:event.bubbles,composed:event.composed}));
      form.dispatchEvent(new CustomEvent('value-changed',{detail:{value:{...form.data,show_active:false,
        show_terms:false,history_page_size:5,duration_hours:12}},bubbles:true,composed:true}));
      const config=editorEvents.at(-1).config;
      editor.setConfig(config);card.setConfig(config);
      const formBefore=editor.shadowRoot.querySelector('ha-form');editor.hass={...mockHass};
      return {stub,initial,schema:form.schema,labels:form.schema.map(s=>form.computeLabel(s)),
        config,bubbles:editorEvents[0].bubbles,composed:editorEvents[0].composed,
        unchangedInput:input.show_active===undefined,
        stableForm:formBefore===editor.shadowRoot.querySelector('ha-form'),
        roundTrip:form.data.show_active===false,hasHass:form.hass.user.id===mockHass.user.id};
    }""")
    assert result["stub"]["pending_entity"] == "sensor.pending"
    assert result["stub"]["active_entity"] == "sensor.active"
    assert result["initial"]["show_history"] is True
    assert result["config"]["show_active"] is False
    assert result["config"]["grid_options"] == {"columns": 6}
    assert result["config"]["duration_hours"] == 12
    for field in ["bubbles", "composed", "unchangedInput", "stableForm", "roundTrip", "hasHass"]:
        assert result[field], field
    schemas = {s["name"]: s for s in result["schema"]}
    assert schemas["pending_entity"]["selector"] == {"entity": {"domain": "sensor"}}
    assert schemas["history_page_size"]["selector"]["number"]["max"] == 50
    assert schemas["show_history"]["selector"] == {"boolean": {}}
    assert all(result["labels"])
    assert await page.get_by_role("button", name="Approve", exact=True).count() == 1
    assert await page.get_by_text("Duration", exact=True).count() == 0
    assert await page.get_by_role("heading", name="Unexpired grants (1)", exact=True).count() == 0
    # Integration-level duration defaults/presets and controller cancellation controls.
    await page.evaluate("""() => {
      mockHass.states['sensor.pending'].attributes.default_duration=8;
      mockHass.states['sensor.pending'].attributes.duration_options=[1,3,8,24,168];
      mockHass.states['sensor.active'].attributes.revoke_supported=true;
      mockHass.callService=async(...args)=>calls.push(args);
      const {duration_hours,...withoutOverride}=baseConfig;
      window.integrationConfig=withoutOverride;
      card.setConfig(withoutOverride);card.hass=mockHass;
    }""")
    await page.get_by_role("button", name="Approve", exact=True).click()
    assert await page.evaluate("calls.at(-1)[2].duration_hours") == 8
    duration = page.get_by_label("Approval duration for <img src=x onerror=alert(1)>", exact=True)
    assert await duration.locator("option").all_text_contents() == ["1h", "3h", "8h", "1 day(s)", "1 week", "Forever"]
    await duration.select_option("168")
    await page.evaluate("mockHass.states['sensor.pending'].last_updated='duration-update';card.hass=mockHass")
    assert await duration.input_value() == "168"
    await page.get_by_role("button", name="Approve", exact=True).click()
    assert await page.evaluate("calls.at(-1)[2].duration_hours") == 168
    await duration.select_option("0")
    await page.get_by_role("button", name="Approve", exact=True).click()
    assert await page.evaluate("calls.at(-1)[2].duration_hours") == 0
    await page.get_by_role("button", name="Cancel access", exact=True).click()
    assert await page.evaluate("calls.at(-1)") == ["omada_guest_access", "revoke_access", {"request_id": "two"}]
    await page.evaluate("mockHass.user.is_admin=false;card.hass=mockHass")
    assert await page.get_by_role("button", name="Cancel access", exact=True).is_disabled()
    assert await duration.is_disabled()
    # A non-admin explicitly selected in the integration options may decide.
    await page.evaluate("""() => {
      mockHass.states['sensor.pending'].attributes.decision_user_ids=[mockHass.user.id];
      card.hass=mockHass;
    }""")
    assert not await page.get_by_role("button", name="Cancel access", exact=True).is_disabled()
    assert not await duration.is_disabled()
    await page.evaluate("""() => {
      mockHass.states['sensor.pending'].attributes.decision_user_ids=[];
      mockHass.user.is_admin=true;card.hass=mockHass;
      mockHass.states['sensor.pending'].attributes.default_duration=12;
      card.setConfig({...integrationConfig,show_duration_selector:false});
    }""")
    assert await page.get_by_role("button", name="Approve", exact=True).count() == 1
    assert await duration.count() == 0
    assert await page.evaluate("""() => {
      const form=editor.shadowRoot.querySelector('ha-form');
      const {duration_hours,...rest}=form.data;
      form.dispatchEvent(new CustomEvent('value-changed',{detail:{value:rest}}));
      return !('duration_hours' in editorEvents.at(-1).config);
    }""")
    # Invalid YAML must fail early instead of sending unsupported WebSocket limits.
    for invalid in [
        {"history_page_size": 0},
        {"history_page_size": 51},
        {"duration_hours": 721},
        {"show_history": "false"},
        {"history_page_size": 1.5},
    ]:
        assert await page.evaluate(
            """bad => {
          try {card.setConfig({...baseConfig,...bad});return false;} catch {return true;}
        }""",
            invalid,
        )
    await page.evaluate("editor.remove()")
    print(
        "Card regressions passed: visual-editor contract, visibility, limits, stable details/focus, identity isolation"
    )


async def check_name_labels(page):
    """Name gestures replace label buttons without turning taps/scrolls into edits."""
    await page.evaluate("""() => {
      mockHass.callService = async (...args) => calls.push(args);
      window.savedPrompt = window.prompt;
      window.labelPrompts = [];
      window.labelReply = '  Family phone  ';
      window.prompt = (...args) => { labelPrompts.push(args); return labelReply; };
    }""")
    names = page.locator('omada-guest-access-card .editable-name')
    assert await names.count() == 2
    for old in ['Label', 'Rename']:
        assert await page.get_by_role('button', name=old, exact=True).count() == 0
    await names.first.click()
    assert await page.evaluate('labelPrompts.length') == 0
    await names.first.dblclick()
    assert await page.evaluate('calls.at(-1)') == [
        'omada_guest_access', 'set_guest_label', {'request_id': 'one', 'label': 'Family phone'}
    ]
    await page.evaluate("labelReply = ''")
    await names.last.focus()
    await page.keyboard.press('Enter')
    assert await page.evaluate('calls.at(-1)[2]') == {'request_id': 'two', 'label': ''}
    await page.evaluate('labelReply = null; window.beforeLabelCalls = calls.length')
    await names.first.dblclick()
    assert await page.evaluate('calls.length === beforeLabelCalls')
    await page.evaluate("labelReply = 'x'.repeat(121)")
    await names.first.dblclick()
    assert await page.get_by_text('Guest label must be at most 120 characters.', exact=True).count() == 1
    assert await page.evaluate('calls.length === beforeLabelCalls')
    await page.evaluate("labelReply = 'Touch label'; card._error = ''; card._render()")

    # Real Chromium touch events exercise mobile pointer dispatch and the hold timer.
    cdp = await page.context.new_cdp_session(page)
    box = await names.last.bounding_box()
    point = {'x': box['x'] + box['width'] / 2, 'y': box['y'] + box['height'] / 2}
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [point]})
    await page.wait_for_timeout(700)
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    assert await page.evaluate('calls.at(-1)[2]') == {'request_id': 'two', 'label': 'Touch label'}
    await page.evaluate('window.beforePrompts = labelPrompts.length')
    # Short tap and a scrolling gesture must not open the editor.
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [point]})
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    await page.wait_for_timeout(700)
    assert await page.evaluate('labelPrompts.length === beforePrompts')
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [point]})
    await cdp.send('Input.dispatchTouchEvent', {
        'type': 'touchMove', 'touchPoints': [{'x': point['x'], 'y': point['y'] + 30}]
    })
    await page.wait_for_timeout(700)
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    assert await page.evaluate('labelPrompts.length === beforePrompts')
    # Redraws and removal cancel outstanding holds rather than editing a stale row.
    for action in ['card._render()', 'card.remove()']:
        await names.first.dispatch_event('pointerdown', {
            'pointerType': 'touch', 'isPrimary': True, 'pointerId': 7, 'clientX': 10, 'clientY': 10
        })
        await page.evaluate(action)
        await page.wait_for_timeout(700)
        assert await page.evaluate('labelPrompts.length === beforePrompts')
        if action == 'card.remove()':
            await page.evaluate('document.body.append(card)')
    await cdp.detach()
    await page.evaluate('card.setConfig({...baseConfig, show_actions:false})')
    assert await names.count() == 0
    await page.evaluate('mockHass.user.is_admin=false; card.setConfig(baseConfig)')
    assert await names.count() == 0
    await page.evaluate("""() => {
      mockHass.states['sensor.pending'].attributes.decision_user_ids = [mockHass.user.id];
      card.hass = mockHass;
    }""")
    assert await names.count() == 2
    await page.evaluate("""() => {
      mockHass.states['sensor.pending'].attributes.decision_user_ids = [];
      mockHass.user.is_admin = true;
      window.prompt = savedPrompt;
      card.setConfig(baseConfig);
    }""")
    print('Name label gestures passed: double-click, keyboard, touch hold, cancellation, permissions')
