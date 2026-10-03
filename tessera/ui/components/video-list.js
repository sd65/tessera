import { sheet } from '../styles.js';

/**
 * The left pane: channel picker, video rows, pager.
 *
 * Presentational. It holds one page of rows and a copy of the query that
 * produced them, and it emits intent — `select` and `filter-change` — which
 * <tessera-app> turns into a request. It never fetches.
 *
 * An earlier draft kept the filters as local state here and filtered the rows
 * in place. That only works while the client holds the whole corpus. Once the
 * list endpoint pages, the list holds one page and cannot filter what it does
 * not have, so the controls now describe a query and <tessera-app> owns it.
 *
 * `filter-change` carries the complete desired query rather than a patch, so
 * there is one rule for what a filter change means instead of a merge rule here
 * and an offset reset there.
 */

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <div class="pane">
    <div class="filter">
      <div class="channels" role="group" aria-label="Channel"></div>
      <label class="toggle">
        <input type="checkbox" class="only-ok">
        <span>only rows with a transcript</span>
      </label>
    </div>
    <div class="rows"></div>
    <div class="pager">
      <button type="button" class="prev">Newer</button>
      <span class="count"></span>
      <button type="button" class="next">Older</button>
    </div>
  </div>
`;

const num = (value) => Number(value).toLocaleString();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class TesseraVideoList extends HTMLElement {
  #filter = { channel: null, status: null, offset: 0 };
  #rows = [];
  #meta = null;
  #selected = null;
  #notice = null;

  #channels;
  #onlyOk;
  #rowsBox;
  #prev;
  #next;
  #count;
  #pager;

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.append(TEMPLATE.content.cloneNode(true));

    this.#channels = this.shadowRoot.querySelector('.channels');
    this.#onlyOk = this.shadowRoot.querySelector('.only-ok');
    this.#rowsBox = this.shadowRoot.querySelector('.rows');
    this.#prev = this.shadowRoot.querySelector('.prev');
    this.#next = this.shadowRoot.querySelector('.next');
    this.#count = this.shadowRoot.querySelector('.count');
    this.#pager = this.shadowRoot.querySelector('.pager');

    this.#channels.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-channel]');
      if (button) this.#request({ channel: button.dataset.channel || null });
    });

    this.#onlyOk.addEventListener('change', () => {
      this.#request({ status: this.#onlyOk.checked ? this.#meta?.ok ?? null : null });
    });

    this.#prev.addEventListener('click', () => this.#step(-1));
    this.#next.addEventListener('click', () => this.#step(+1));
  }

  // ---- properties in ---------------------------------------------------

  get filter() { return { ...this.#filter }; }
  set filter(value) { this.#filter = { ...this.#filter, ...(value || {}) }; this.#render(); }

  get rows() { return this.#rows; }
  set rows(value) { this.#rows = value || []; this.#render(); }

  get meta() { return this.#meta; }
  set meta(value) { this.#meta = value || null; this.#render(); }

  get selected() { return this.#selected; }
  set selected(value) {
    this.#selected = value || null;
    // Reflected rather than stored as an attribute: this is the selection the
    // user is looking at, and `aria-current` is what assistive tech reads.
    for (const row of this.#rowsBox.children) {
      row.setAttribute('aria-current', String(row.dataset.videoId === this.#selected));
    }
    this.#rowsBox
      .querySelector(`[data-video-id="${CSS.escape(this.#selected ?? '')}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }

  get notice() { return this.#notice; }
  set notice(value) { this.#notice = value || null; this.#render(); }

  // ---- events out ------------------------------------------------------

  #emitSelect(videoId) {
    this.dispatchEvent(new CustomEvent('select', {
      detail: { videoId }, bubbles: true, composed: true,
    }));
  }

  /**
   * Ask for a different query. `filter-change` carries the whole query, and
   * changing a filter resets the offset: without that, picking a channel while
   * on page 3 lands on an empty page of a set that has one.
   */
  #request(patch) {
    const next = 'offset' in patch ? patch : { ...patch, offset: 0 };
    this.dispatchEvent(new CustomEvent('filter-change', {
      detail: { ...this.#filter, ...next }, bubbles: true, composed: true,
    }));
  }

  #step(direction) {
    const limit = this.#meta?.limit || 1;
    this.#request({ offset: Math.max(0, this.#filter.offset + direction * limit) });
  }

  // ---- render ----------------------------------------------------------

  /** Mirrors cli.py: permanent iff listed permanent, everything else is retried. */
  #statusKind(status) {
    if (this.#meta && status === this.#meta.ok) return 'ok';
    if (this.#meta && this.#meta.permanent.includes(status)) return 'permanent';
    return 'retryable';
  }

  #exhausted(row) {
    const meta = this.#meta;
    return Boolean(meta)
      && meta.retryable.includes(row.status)
      && row.attempts >= meta.max_attempts;
  }

  #renderChannels() {
    this.#channels.textContent = '';
    const all = el('button', null, 'all channels');
    all.type = 'button';
    all.dataset.channel = '';
    all.setAttribute('aria-pressed', String(!this.#filter.channel));
    this.#channels.append(all);

    for (const channel of this.#meta?.channels || []) {
      const button = el('button', null, channel.name);
      button.type = 'button';
      button.dataset.channel = channel.id;
      button.setAttribute('aria-pressed', String(this.#filter.channel === channel.id));
      this.#channels.append(button);
    }
  }

  #renderRows() {
    this.#rowsBox.textContent = '';

    if (this.#notice) {
      this.#rowsBox.append(el('p', 'notice', this.#notice));
      return;
    }
    if (!this.#rows.length) {
      this.#rowsBox.append(el('p', 'notice', 'No videos match this filter.'));
      return;
    }

    for (const row of this.#rows) {
      const button = el('button', 'row');
      button.type = 'button';
      button.dataset.videoId = row.video_id;
      button.setAttribute('aria-current', String(row.video_id === this.#selected));
      button.addEventListener('click', () => this.#emitSelect(row.video_id));

      button.append(el('span', 'title', row.title || row.video_id));

      const meta = el('span', 'meta');
      meta.append(el('span', null, row.channel_name || row.channel_id));
      meta.append(el('span', null, (row.published_at || '').slice(0, 10)));

      // Keyed on status, not on `chars != null`. A failed row stores chars as
      // integer 0, not NULL, so a null test would report every one of them as
      // a video with a 0-character transcript instead of as a failure. The
      // status is the truth, and meta.ok publishes it.

      if (row.status === this.#meta?.ok) {
        meta.append(el('span', null, `${num(row.chars)} chars`));
        if (row.kind) meta.append(el('span', null, row.kind));
      } else if (row.status) {
        const status = el('span', `status is-${this.#statusKind(row.status)}`, row.status);
        status.append(el('span', null, `x${row.attempts ?? 0}`));
        if (this.#exhausted(row)) status.append(el('span', null, 'no longer retried'));
        meta.append(status);
      }

      button.append(meta);
      this.#rowsBox.append(button);
    }
  }

  #renderPager() {
    const meta = this.#meta;
    this.#pager.hidden = !meta;

    // Guarded before touching meta.offset: the first thing that ever happens is
    // `notice = 'Loading…'`, and at that point there is no envelope yet.
    if (!meta) {
      this.#count.textContent = '';
      this.#prev.disabled = true;
      this.#next.disabled = true;
      return;
    }

    const shown = this.#rows.length;
    this.#count.textContent = `${shown ? meta.offset + 1 : 0}–${meta.offset + shown} of ${num(meta.total)}`;
    this.#prev.disabled = meta.offset <= 0;
    this.#next.disabled = !meta.has_more;
  }

  #render() {
    this.#renderChannels();
    this.#onlyOk.checked = Boolean(this.#meta && this.#filter.status === this.#meta.ok);
    this.#onlyOk.disabled = !this.#meta;
    this.#renderRows();
    this.#renderPager();
  }
}

customElements.define('tessera-video-list', TesseraVideoList);