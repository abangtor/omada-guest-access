/* No external dependencies; guest content is only inserted via textContent. */
class OmadaGuestAccessCard extends HTMLElement {
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
  }
  setConfig(config) {
    if (!config.pending_entity || !config.active_entity) {
      throw new Error('Set pending_entity and active_entity to the integration sensors.');
    }
    const hours = config.duration_hours ?? 8;
    if (!Number.isInteger(hours) || hours < 1 || hours > 720) {
      throw new Error('duration_hours must be an integer between 1 and 720.');
    }
    this._historyGeneration++;
    this._history = null;
    this._historyOpen = false;
    this._historyLoading = false;
    this._historyOffset = 0;
    this._historyError = '';
    this._config = {...config, duration_hours: hours};
    this._render();
  }
  set hass(hass) {
    const previousEntry = this._hass?.states[this._config?.pending_entity]?.attributes.entry_id;
    const nextEntry = hass.states[this._config?.pending_entity]?.attributes.entry_id;
    if (hass.user?.id !== this._hass?.user?.id || !hass.user?.is_admin || previousEntry !== nextEntry) {
      this._historyGeneration++;
      this._history = null;
      this._historyOpen = false;
      this._historyLoading = false;
      this._historyError = '';
      this._historyOffset = 0;
    }
    this._hass = hass; this._render();
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
    root.replaceChildren();
    this._node('style', `ha-card{padding:20px}h2{margin:0 0 16px}h3{margin-bottom:8px}
      section{padding:12px 0;border-top:1px solid var(--divider-color)}p{white-space:pre-wrap;overflow-wrap:anywhere}
      button{margin:4px 8px 4px 0;padding:8px 14px;cursor:pointer;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px}
      select,input{padding:8px;max-width:100%;box-sizing:border-box}label{display:block;margin:8px 0}details{margin:8px 0}
      button:disabled{opacity:.5;cursor:default}.error{color:var(--error-color)}small{color:var(--secondary-text-color)}`, root);
    const card = this._node('ha-card', undefined, root);
    this._node('h2', this._config.title || 'Guest Wi-Fi requests', card);
    if (this._error) this._node('p', this._error, card).className = 'error';
    const pending = this._hass.states[this._config.pending_entity];
    const active = this._hass.states[this._config.active_entity];
    if (!pending || !active || [pending.state, active.state].some(s => ['unavailable', 'unknown'].includes(s))) {
      this._node('p', 'Guest access sensors are unavailable. Check the integration and card entity IDs.', card);
      return;
    }
    const admin = this._hass.user?.is_admin === true;
    if (!admin) this._node('p', 'Only Home Assistant administrators can make access decisions.', card);
    const requests = pending.attributes.requests || [];
    this._node('h3', `Pending (${requests.length})`, card);
    if (!requests.length) this._node('p', 'No requests waiting.', card);
    for (const item of requests) {
      const row = this._node('section', undefined, card);
      this._node('strong', item.guest_name || 'Guest', row);
      this._node('p', item.note || '', row);
      this._node('small', `${item.client_mac} · expires ${this._date(item.expires_at)}`, row);
      this._node('br', undefined, row);
      this._button(row, `Approve ${this._config.duration_hours}h`, 'approve_request', item,
        {duration_hours: this._config.duration_hours}, !admin);
      this._button(row, 'Deny', 'deny_request', item, {}, !admin);
    }
    const sessions = active.attributes.sessions || [];
    this._node('h3', `Unexpired grants (${sessions.length})`, card);
    this._node('small', 'Local grant records; not a live controller client list.', card);
    for (const item of sessions) {
      const row = this._node('section', undefined, card);
      this._node('strong', item.guest_name || 'Guest', row);
      this._node('p', `${item.client_mac} · until ${this._date(item.access_expires_at)}`, row);
      if (active.attributes.revoke_supported) this._button(row, 'Revoke', 'revoke_access', item, {}, !admin);
    }
    if (sessions.length && !active.attributes.revoke_supported) {
      this._node('p', 'To end access early, use Omada Hotspot Manager. This API does not support revocation.', card);
    }
    if (admin && pending.attributes.entry_id) this._renderHistory(card);
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
    this._node('small', 'Retained local request records, not a controller audit log. Refresh to see changes.', card);
    if (!this._historyOpen) {
      this._historyButton(card, 'Show history', () => {
        this._historyOpen = true; this._historyOffset = 0; this._loadHistory();
      });
      return;
    }
    const form = this._node('form', undefined, card);
    const statusLabel = this._node('label', 'Status ', form);
    const select = this._node('select', undefined, statusLabel);
    select.setAttribute('aria-label', 'History status');
    for (const value of ['', 'pending', 'approved', 'denied', 'expired', 'revoked']) {
      const option = this._node('option', value || 'All statuses', select); option.value = value;
    }
    select.value = this._historyStatus;
    select.onchange = () => { this._historyStatus = select.value; };
    const queryLabel = this._node('label', 'Name, MAC or request ID ', form);
    const query = this._node('input', undefined, queryLabel);
    query.setAttribute('aria-label', 'Search history');
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
      this._node('p', `${item.client_mac} · requested ${this._date(item.created_at)}`, row);
      const details = this._node('details', undefined, row);
      this._node('summary', 'Request details', details);
      this._node('p', `Request ID: ${item.request_id}`, details);
      if (item.note) this._node('p', item.note, details);
      this._node('p', `Last updated: ${this._date(item.updated_at)}`, details);
      if (item.access_expires_at) this._node('p', `Grant until: ${this._date(item.access_expires_at)}`, details);
      this._node('p', `Decision user ID: ${item.decision_user_id || 'Automation / not recorded'}`, details);
      if (item.denial_reason) this._node('p', `Denial reason: ${item.denial_reason}`, details);
      if (item.terms_accepted_at) {
        this._node('p', `Terms accepted: ${this._date(item.terms_accepted_at)}`, details);
        this._node('p', `Terms SHA-256: ${item.terms_version}`, details);
        this._node('p', item.terms_text, details);
      } else this._node('p', 'Terms acceptance was not recorded for this request.', details);
    }
    this._historyButton(card, 'Previous', () => {
      this._historyOffset = Math.max(0, offset - limit); this._loadHistory();
    }, this._historyLoading || offset === 0);
    this._historyButton(card, 'Next', () => {
      this._historyOffset = offset + limit; this._loadHistory();
    }, this._historyLoading || offset + limit >= total);
  }
  async _loadHistory() {
    if (!this._hass?.user?.is_admin) return;
    const entryId = this._hass.states[this._config.pending_entity]?.attributes.entry_id;
    if (!entryId) return;
    const generation = ++this._historyGeneration;
    this._historyLoading = true; this._historyError = ''; this._history = null; this._render();
    try {
      const result = await this._hass.callWS({type: 'omada_guest_access/history', entry_id: entryId,
        offset: this._historyOffset, limit: 20, query: this._historyQuery,
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
  _date(value) { return value ? new Date(value).toLocaleString() : '—'; }
  _button(row, label, service, item, data, disabled) {
    const button = this._node('button', label, row);
    button.disabled = disabled || this._busy.has(item.request_id);
    button.addEventListener('click', async () => {
      if (this._busy.has(item.request_id)) return;
      this._busy.add(item.request_id); this._error = ''; this._render();
      try {
        await this._hass.callService('omada_guest_access', service, {request_id: item.request_id, ...data});
      } catch (err) {
        this._error = err.message || 'The access decision failed. Please try again.';
      } finally {
        this._busy.delete(item.request_id); this._render();
      }
    });
  }
}
if (!customElements.get('omada-guest-access-card')) customElements.define('omada-guest-access-card', OmadaGuestAccessCard);
window.customCards = window.customCards || [];
window.customCards.push({type: 'omada-guest-access-card', name: 'Omada Guest Access', description: 'Review and approve guest Wi-Fi requests.'});
