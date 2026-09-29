/* No external dependencies; guest content is only inserted via textContent. */
class OmadaGuestAccessCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._busy = new Set();
    this._error = '';
  }
  setConfig(config) {
    if (!config.pending_entity || !config.active_entity) {
      throw new Error('Set pending_entity and active_entity to the integration sensors.');
    }
    const hours = config.duration_hours ?? 8;
    if (!Number.isInteger(hours) || hours < 1 || hours > 720) {
      throw new Error('duration_hours must be an integer between 1 and 720.');
    }
    this._config = {...config, duration_hours: hours};
    this._render();
  }
  set hass(hass) { this._hass = hass; this._render(); }
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
