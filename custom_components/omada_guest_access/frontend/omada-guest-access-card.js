/* No external dependencies; guest content is only inserted via textContent. */
const CARD_DEFAULTS = {
  history_page_size: 20,
  show_title: true, show_pending: true, show_active: true, show_history: true,
  show_notes: true, show_mac: true, show_timestamps: true, show_actions: true,
  show_notices: true, show_empty: true, show_duration_selector: true, show_history_details: true,
  show_decision_user: true, show_terms: true, compact: false,
};
const CARD_LABELS = {
  title: 'Title', pending_entity: 'Pending requests sensor', active_entity: 'Active sessions sensor',
  duration_hours: 'Default approval override in hours (blank: integration default)', history_page_size: 'History requests per page',
  show_title: 'Show title', show_pending: 'Show pending requests', show_active: 'Show unexpired grants',
  show_history: 'Show request history (administrators only)', show_notes: 'Show guest notes',
  show_mac: 'Show MAC addresses', show_timestamps: 'Show timestamps', show_actions: 'Show action buttons',
  show_duration_selector: 'Show alternative approval duration dropdown', show_notices: 'Show explanatory notices', show_empty: 'Show empty sections',
  show_history_details: 'Show expandable history details', show_decision_user: 'Show decision user IDs',
  show_terms: 'Show consent and terms details', compact: 'Compact spacing',
};
const cardSchema = [
  {name: 'title', selector: {text: {}}},
  ...['pending_entity', 'active_entity'].map(name => ({name, required: true, selector: {entity: {domain: 'sensor'}}})),
  {name: 'duration_hours', selector: {number: {min: 0, max: 720, step: 1, mode: 'box'}}},
  {name: 'history_page_size', selector: {number: {min: 1, max: 50, step: 1, mode: 'box'}}},
  ...Object.keys(CARD_DEFAULTS).filter(name => typeof CARD_DEFAULTS[name] === 'boolean')
    .map(name => ({name, selector: {boolean: {}}})),
];
class OmadaGuestAccessCardEditor extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode: 'open'});
    const note = document.createElement('p');
    note.textContent = 'Visibility options change presentation only, not Home Assistant permissions. Both sensors are required, even when a section is hidden.';
    this._form = document.createElement('ha-form');
    this._form.schema = cardSchema;
    this._form.computeLabel = schema => CARD_LABELS[schema.name] || schema.name;
    this._form.addEventListener('value-changed', event => {
      event.stopPropagation();
      const value = event.detail.value;
      this._config = {...this._config, ...value};
      // ha-form emits the complete form data; clearing an optional override
      // must remove the old key instead of silently retaining its prior value.
      for (const field of cardSchema) if (!(field.name in value)) delete this._config[field.name];
      this.dispatchEvent(new CustomEvent('config-changed', {
        detail: {config: {...this._config}}, bubbles: true, composed: true,
      }));
    });
    this.shadowRoot.append(note, this._form);
  }
  setConfig(config) {
    this._config = {...config};
    this._form.data = {...CARD_DEFAULTS, ...config};
  }
  set hass(hass) { this._form.hass = hass; }
}
class OmadaGuestAccessCard extends HTMLElement {
  static async getConfigElement() {
    // Load HA's native form/selectors when this is the first editor opened.
    if (!customElements.get('ha-form') && window.loadCardHelpers) {
      const helpers = await window.loadCardHelpers();
      const nativeCard = helpers.createCardElement({type: 'button', entity: 'sun.sun'});
      await nativeCard.constructor.getConfigElement();
    }
    return document.createElement('omada-guest-access-card-editor');
  }
  static getStubConfig(hass) {
    const states = Object.values(hass?.states || {});
    const pending = states.find(state => state.attributes?.entry_id && Array.isArray(state.attributes.requests));
    const active = states.find(state => state.attributes?.entry_id === pending?.attributes.entry_id && Array.isArray(state.attributes.sessions));
    return {type: 'custom:omada-guest-access-card', pending_entity: pending?.entity_id || '',
      active_entity: active?.entity_id || '', title: 'Guest Wi-Fi'};
  }
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._busy = new Set();
    this._error = '';
    this._history = null;
    this._historyOffset = 0;
    this._historyStatus = '';
    this._historyQuery = '';
    this._historyOpen = false;
    this._historyLoading = false;
    this._historyError = '';
    this._historyGeneration = 0;
    this._expandedHistory = new Map();
    this._selectedDurations = new Map();
    this._pendingState = undefined;
    this._activeState = undefined;
    this._renderQueued = false;
  }
  setConfig(config) {
    if (!config.pending_entity || !config.active_entity) {
      throw new Error('Set pending_entity and active_entity to the integration sensors.');
    }
    const hours = config.duration_hours ?? null;
    if (hours !== null && (!Number.isInteger(hours) || hours < 0 || hours > 720)) {
      throw new Error('duration_hours must be 0 (no expiry) or an integer between 1 and 720.');
    }
    const pageSize = config.history_page_size ?? CARD_DEFAULTS.history_page_size;
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 50) {
      throw new Error('history_page_size must be an integer between 1 and 50.');
    }
    for (const [key, value] of Object.entries(CARD_DEFAULTS)) {
      if (typeof value === 'boolean' && config[key] !== undefined && typeof config[key] !== 'boolean') {
        throw new Error(`${key} must be true or false.`);
      }
    }
    this._clearHistoryDetails();
    this._selectedDurations.clear();
    this._historyGeneration++;
    this._history = null;
    this._historyOpen = false;
    this._historyLoading = false;
    this._historyOffset = 0;
    this._historyError = '';
    this._historyQuery = ''; this._historyStatus = '';
    this._error = '';
    this._config = {...CARD_DEFAULTS, ...config, duration_hours: hours, history_page_size: pageSize};
    this._pendingState = undefined;
    this._activeState = undefined;
    if (this._hass) this.hass = this._hass;
  }
  set hass(hass) {
    if (!this._config) { this._hass = hass; return; }
    const pending = hass.states[this._config.pending_entity];
    const active = hass.states[this._config.active_entity];
    const entry = pending?.attributes?.entry_id;
    const identity = JSON.stringify([hass.user?.id, hass.user?.is_admin === true, entry]);
    // Also notice the small permission flag when a test harness or an
    // integration mutates attributes in place. Normal HA state updates replace
    // the entity object, so this remains a lightweight check.
    const permissionMarker = (pending?.attributes?.decision_user_ids || []).join('|');
    if (identity !== this._identity) {
      this._identity = identity;
      this._historyGeneration++;
      this._clearHistoryDetails();
      this._selectedDurations.clear();
      this._history = null;
      this._historyOpen = false;
      this._historyLoading = false;
      this._historyError = '';
      this._historyOffset = 0;
      this._historyQuery = ''; this._historyStatus = '';
    }
    this._hass = hass;
    // HA creates a new hass object for every state event. Never stringify the
    // complete request/session attributes here: unrelated events must not
    // cause expensive work that can stall a mobile dashboard.
    if (pending === this._pendingState && active === this._activeState &&
        identity === this._renderIdentity && permissionMarker === this._permissionMarker) return;
    this._pendingState = pending;
    this._activeState = active;
    this._renderIdentity = identity;
    this._permissionMarker = permissionMarker;
    this._queueRender();
  }
  _queueRender() {
    if (this._renderQueued) return;
    this._renderQueued = true;
    // Rendering synchronously preserves Lovelace's normal card lifecycle.
    // The lightweight state-reference check in hass() avoids redraws for
    // unrelated HA state events.
    try { this._render(); }
    catch (err) { this._renderFailure(err); }
    finally { this._renderQueued = false; }
  }
  _renderFailure(err) {
    const root = this.shadowRoot;
    root.replaceChildren();
    this._node('style', 'ha-card{padding:16px}.error{color:var(--error-color)}', root);
    const card = this._node('ha-card', undefined, root);
    this._node('p', 'Omada Guest Access card could not render. Reload the dashboard after updating the card.', card).className = 'error';
    // Keep the useful diagnostic in the browser console without leaking guest data.
    console.error('Omada Guest Access card render failed', err);
  }
  _clearHistoryDetails() {
    this._expandedHistory.clear();
    // Do not recapture another user's or another entry's disclosure state.
    this.shadowRoot.querySelectorAll('details[data-request-id]').forEach(el => el.removeAttribute('data-request-id'));
  }
  getCardSize() { return 5; }
  _node(tag, text, parent) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (parent) parent.append(element);
    return element;
  }
  _render() {
    if (!this._hass || !this._config) return;
    const root = this.shadowRoot;
    const focused = root.activeElement;
    const focusState = focused?.id ? {id: focused.id, start: focused.selectionStart, end: focused.selectionEnd} : null;
    root.querySelectorAll('details[data-request-id]').forEach(el => {
      this._expandedHistory.set(el.dataset.requestId, el.open);
    });
    // Bound disclosure state while browsing many pages.
    while (this._expandedHistory.size > 500) this._expandedHistory.delete(this._expandedHistory.keys().next().value);
    root.replaceChildren();
    this._node('style', `ha-card{padding:${this._config.compact ? '12px' : '20px'}}h2{margin:0 0 16px}h3{margin-bottom:8px}
      section{padding:${this._config.compact ? '6px' : '12px'} 0;border-top:1px solid var(--divider-color)}p{white-space:pre-wrap;overflow-wrap:anywhere}
      .guest-row{display:grid;grid-template-columns:minmax(150px,1fr) auto;gap:8px;align-items:center}.guest-info p{margin:4px 0}.actions{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:6px}
      button{margin:0;padding:8px 12px;cursor:pointer;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;white-space:nowrap}
      select,input{padding:8px;max-width:100%;box-sizing:border-box}label{display:block;margin:8px 0}details{margin:8px 0}.actions label{margin:0;display:flex;align-items:center;gap:4px;white-space:nowrap}
      @media(max-width:600px){.guest-row{grid-template-columns:1fr}.actions{justify-content:flex-start}}
      button:disabled{opacity:.5;cursor:default}.error{color:var(--error-color)}small{color:var(--secondary-text-color)}`, root);
    const card = this._node('ha-card', undefined, root);
    if (this._config.show_title) this._node('h2', this._config.title ?? 'Guest Wi-Fi requests', card);
    if (this._error) this._node('p', this._error, card).className = 'error';
    const pending = this._hass.states[this._config.pending_entity];
    const active = this._hass.states[this._config.active_entity];
    if (!pending || !active || [pending.state, active.state].some(s => ['unavailable', 'unknown'].includes(s))) {
      this._node('p', 'Guest access sensors are unavailable. Check the integration and card entity IDs.', card);
      return;
    }
    const admin = this._hass.user?.is_admin === true;
    const canDecide = admin || (pending.attributes.decision_user_ids || []).includes(this._hass.user?.id);
    if (!canDecide && this._config.show_notices) this._node('p', 'You do not have permission to make guest access decisions.', card);
    const requests = pending.attributes.requests || [];
    const configuredDefault = pending.attributes.default_duration;
    const defaultHours = this._config.duration_hours ??
      (Number.isInteger(configuredDefault) && configuredDefault >= 1 && configuredDefault <= 720 ? configuredDefault : 8);
    const presets = [...new Set([...(pending.attributes.duration_options || [1, 2, 4, 8, 12, 24, 48, 72, 168]), 0])]
      .filter(value => Number.isInteger(value) && value >= 0 && value <= 168);
    const currentIds = new Set(requests.map(item => item.request_id));
    for (const id of this._selectedDurations.keys()) if (!currentIds.has(id)) this._selectedDurations.delete(id);
    if (this._config.show_pending && (requests.length || this._config.show_empty)) {
      this._node('h3', `Pending (${requests.length})`, card);
      if (!requests.length) this._node('p', 'No requests waiting.', card);
      for (const item of requests) {
        const row = this._node('section', undefined, card); row.className = 'guest-row';
        const info = this._node('div', undefined, row); info.className = 'guest-info';
        this._node('strong', item.guest_name || 'Guest', info);
        if (this._config.show_notes && item.note) this._node('p', item.note, info);
        this._metadata(info, item.client_mac, item.expires_at, 'expires');
        if (this._config.show_actions) {
          const actions = this._node('div', undefined, row); actions.className = 'actions';
          if (this._config.show_duration_selector && presets.length) {
            const select = this._node('select', undefined, actions);
            select.id = `duration-${item.request_id}`;
            select.setAttribute('aria-label', `Approval duration for ${item.guest_name || 'Guest'}`);
            for (const hours of presets) {
              const text = hours === 0 ? 'Forever' : hours === 168 ? '1 week' : hours >= 24 && hours % 24 === 0 ? `${hours / 24} day(s)` : `${hours}h`;
              const option = this._node('option', text, select); option.value = String(hours);
            }
            const selected = this._selectedDurations.get(item.request_id);
            const hours = presets.includes(selected) ? selected : presets.includes(defaultHours) ? defaultHours : presets[0];
            select.value = String(hours);
            this._selectedDurations.set(item.request_id, hours);
            select.disabled = !canDecide || this._busy.has(item.request_id);
            select.onchange = () => this._selectedDurations.set(item.request_id, Number(select.value));
            this._button(actions, 'Approve', 'approve_request', item,
              () => ({duration_hours: this._selectedDurations.get(item.request_id)}), !canDecide);
          } else {
            this._button(actions, 'Approve', 'approve_request', item,
              {duration_hours: defaultHours}, !canDecide);
          }
          this._button(actions, 'Deny', 'deny_request', item, {}, !canDecide);
        }
      }
    }
    const sessions = active.attributes.sessions || [];
    if (this._config.show_active && (sessions.length || this._config.show_empty)) {
      this._node('h3', `Unexpired grants (${sessions.length})`, card);
      if (this._config.show_notices) this._node('small', 'Local grant records; not a live controller client list.', card);
      for (const item of sessions) {
        const row = this._node('section', undefined, card); row.className = 'guest-row';
        const info = this._node('div', undefined, row); info.className = 'guest-info';
        this._node('strong', item.guest_name || 'Guest', info);
        this._metadata(info, item.client_mac, item.access_expires_at, 'until');
        if (this._config.show_actions && active.attributes.revoke_supported) {
          const actions = this._node('div', undefined, row); actions.className = 'actions';
          this._button(actions, 'Cancel access', 'revoke_access', item, {}, !canDecide);
        }
      }
      if (this._config.show_notices && sessions.length && !active.attributes.revoke_supported) {
        this._node('p', 'To end access early, use Omada Hotspot Manager. Deauthentication is disabled in integration options.', card);
      }
    }
    if (this._config.show_history && admin && pending.attributes.entry_id) this._renderHistory(card);
    if (focusState) {
      const replacement = root.getElementById(focusState.id);
      replacement?.focus({preventScroll: true});
      if (replacement?.tagName === 'INPUT' && focusState.start !== null) {
        replacement.setSelectionRange(focusState.start, focusState.end);
      }
    }
  }
  _historyButton(parent, label, action, disabled = false) {
    const button = this._node('button', label, parent);
    button.type = 'button';
    button.disabled = disabled;
    button.addEventListener('click', action);
    return button;
  }
  _renderHistory(card) {
    this._node('h3', 'Request history', card);
    if (this._config.show_notices) this._node('small', 'Retained local request records, not a controller audit log. Refresh to see changes.', card);
    if (!this._historyOpen) {
      this._historyButton(card, 'Show history', () => {
        this._historyOpen = true; this._historyOffset = 0; this._loadHistory();
      });
      return;
    }
    const form = this._node('form', undefined, card);
    const statusLabel = this._node('label', 'Status ', form);
    const select = this._node('select', undefined, statusLabel);
    select.setAttribute('aria-label', 'History status'); select.id = 'history-status';
    for (const value of ['', 'pending', 'approved', 'denied', 'expired', 'revoked']) {
      const option = this._node('option', value || 'All statuses', select); option.value = value;
    }
    select.value = this._historyStatus;
    select.onchange = () => { this._historyStatus = select.value; };
    const queryLabel = this._node('label', 'Name, MAC or request ID ', form);
    const query = this._node('input', undefined, queryLabel);
    query.setAttribute('aria-label', 'Search history'); query.id = 'history-query';
    query.maxLength = 120; query.value = this._historyQuery;
    query.oninput = () => { this._historyQuery = query.value; };
    const search = this._node('button', 'Search / refresh', form);
    search.type = 'submit'; search.disabled = this._historyLoading;
    form.onsubmit = event => { event.preventDefault(); this._historyOffset = 0; this._loadHistory(); };
    this._historyButton(card, 'Hide history', () => {
      this._historyGeneration++; this._historyLoading = false; this._historyOpen = false;
      this._history = null; this._render();
    });
    if (this._historyError) this._node('p', this._historyError, card).className = 'error';
    if (this._historyLoading) this._node('p', 'Loading history…', card);
    if (!this._history) return;
    const {requests, total, offset, limit} = this._history;
    this._node('p', total ? `Showing ${offset + 1}–${Math.min(offset + requests.length, total)} of ${total}` : 'No matching requests.', card);
    for (const item of requests) {
      const row = this._node('section', undefined, card);
      this._node('strong', `${item.guest_name || 'Guest'} · ${item.status}`, row);
      this._metadata(row, item.client_mac, item.created_at, 'requested');
      if (!this._config.show_history_details) continue;
      const details = this._node('details', undefined, row);
      details.dataset.requestId = item.request_id;
      details.open = this._expandedHistory.get(item.request_id) === true;
      this._node('summary', 'Request details', details);
      this._node('p', `Request ID: ${item.request_id}`, details);
      if (this._config.show_notes && item.note) this._node('p', item.note, details);
      if (this._config.show_timestamps) this._node('p', `Last updated: ${this._date(item.updated_at)}`, details);
      if (this._config.show_timestamps && item.access_expires_at) this._node('p', `Grant until: ${this._date(item.access_expires_at)}`, details);
      if (this._config.show_decision_user) this._node('p', `Decision user ID: ${item.decision_user_id || 'Automation / not recorded'}`, details);
      if (item.denial_reason) this._node('p', `Denial reason: ${item.denial_reason}`, details);
      if (this._config.show_terms) {
        if (item.terms_accepted_at) {
          if (this._config.show_timestamps) this._node('p', `Terms accepted: ${this._date(item.terms_accepted_at)}`, details);
          this._node('p', `Terms SHA-256: ${item.terms_version}`, details);
          this._node('p', item.terms_text, details);
        } else this._node('p', 'Terms acceptance was not recorded for this request.', details);
      }
    }
    this._historyButton(card, 'Previous', () => {
      this._historyOffset = Math.max(0, offset - limit); this._loadHistory();
    }, this._historyLoading || offset === 0);
    this._historyButton(card, 'Next', () => {
      this._historyOffset = offset + limit; this._loadHistory();
    }, this._historyLoading || offset + limit >= total);
  }
  async _loadHistory() {
    if (!this._config.show_history || !this._hass?.user?.is_admin) return;
    const entryId = this._hass.states[this._config.pending_entity]?.attributes.entry_id;
    if (!entryId) return;
    const generation = ++this._historyGeneration;
    this._historyLoading = true; this._historyError = ''; this._history = null; this._render();
    try {
      const result = await this._hass.callWS({type: 'omada_guest_access/history', entry_id: entryId,
        offset: this._historyOffset, limit: this._config.history_page_size, query: this._historyQuery,
        ...(this._historyStatus ? {status: this._historyStatus} : {})});
      if (generation !== this._historyGeneration) return;
      this._history = result;
    } catch (err) {
      if (generation !== this._historyGeneration) return;
      this._historyError = err.message || 'Unable to load request history.';
    } finally {
      if (generation === this._historyGeneration) { this._historyLoading = false; this._render(); }
    }
  }
  _metadata(parent, mac, timestamp, label) {
    const parts = [];
    if (this._config.show_mac && mac) parts.push(mac);
    if (this._config.show_timestamps) parts.push(timestamp ? `${label} ${this._date(timestamp)}` : label === 'until' ? 'no expiry' : `${label} —`);
    if (parts.length) this._node('p', parts.join(' · '), parent);
  }
  _date(value) { return value ? new Date(value).toLocaleString() : '—'; }
  _button(row, label, service, item, data, disabled) {
    const button = this._node('button', label, row);
    button.disabled = disabled || this._busy.has(item.request_id);
    button.addEventListener('click', async () => {
      if (this._busy.has(item.request_id)) return;
      const serviceData = typeof data === 'function' ? data() : data;
      this._busy.add(item.request_id); this._error = ''; this._render();
      try {
        await this._hass.callService('omada_guest_access', service, {request_id: item.request_id, ...serviceData});
      } catch (err) {
        this._error = err.message || 'The access decision failed. Please try again.';
      } finally {
        this._busy.delete(item.request_id); this._render();
      }
    });
  }
}
if (!customElements.get('omada-guest-access-card-editor')) customElements.define('omada-guest-access-card-editor', OmadaGuestAccessCardEditor);
if (!customElements.get('omada-guest-access-card')) customElements.define('omada-guest-access-card', OmadaGuestAccessCard);
window.customCards = window.customCards || [];
window.customCards.push({type: 'omada-guest-access-card', name: 'Omada Guest Access', description: 'Review and approve guest Wi-Fi requests.', preview: true});
