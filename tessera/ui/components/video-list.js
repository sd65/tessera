import { sheet } from '../styles.js';
import { relativeDay, sentenceCase } from '../format.js';
import { el } from './el.js';

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
 *
 * Rows do not show transcript status. Failed videos used to wear a coloured
 * status badge here, and there was a checkbox for hiding them; both are gone.
 * The recommendation dot is separate metadata from a successful summary, not a
 * transcript-status badge.
 */

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <div class="pane">
    <div class="filter">
      <input class="combo" type="text" list="channels" autocomplete="off"
             spellcheck="false" aria-label="channel" placeholder="all channels">
      <datalist id="channels"></datalist>
    </div>
    <div class="rows"></div>
    <div class="pager">
      <button type="button" class="prev">Newer</button>
      <span class="count"></span>
      <button type="button" class="next">Older</button>
    </div>
  </div>
`;

// An <input> backed by a <datalist> is a native searchable combo: the browser
// owns the popup, the typeahead and the keyboard behaviour, and the page ships
// no code for any of it. This replaced a row of pill buttons, which looked like
// a control and was not one — no typeahead, no keyboard, and it grows without
// bound as the channel count does. Two channels today, one more per feed added.
// `list` has been here since Chrome 20 and Safari 12.1; a native <select> is the
// fallback, and it is still better than the pills.
const HAS_COMBO = 'list' in HTMLInputElement.prototype;

const num = (value) => Number(value).toLocaleString();

const RECOMMENDATION_LABELS = {
  en: {
    summary_sufficient: 'Summary is sufficient',
    selected_passages: 'Selected passages recommended',
    full_video_recommended: 'Full video recommended',
    undetermined: 'Viewing recommendation undetermined',
  },
  fr: {
    summary_sufficient: 'Le résumé suffit',
    selected_passages: 'Passages sélectionnés conseillés',
    full_video_recommended: 'Visionnage complet recommandé',
    undetermined: 'Recommandation indéterminée',
  },
};

export class TesseraVideoList extends HTMLElement {
  #filter = { channel: null, offset: 0 };
  #rows = [];
  #meta = null;
  #selected = null;
  #notice = null;

  #combo;
  #datalist;
  #fallback;
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

    this.#combo = this.shadowRoot.querySelector('.combo');
    this.#datalist = this.shadowRoot.querySelector('datalist');
    this.#rowsBox = this.shadowRoot.querySelector('.rows');
    this.#prev = this.shadowRoot.querySelector('.prev');
    this.#next = this.shadowRoot.querySelector('.next');
    this.#count = this.shadowRoot.querySelector('.count');
    this.#pager = this.shadowRoot.querySelector('.pager');

    if (HAS_COMBO) {
      this.#combo.addEventListener('change', () => this.#picked(this.#combo.value));
    } else {
      this.#fallback = el('select');
      this.#fallback.addEventListener('change', () => this.#picked(this.#fallback.value, true));
      this.#combo.replaceWith(this.#fallback);
    }

    this.#prev.addEventListener('click', () => this.#step(-1));
    this.#next.addEventListener('click', () => this.#step(+1));
  }

  // ---- properties in ---------------------------------------------------

  set filter(value) { this.#filter = { ...this.#filter, ...(value || {}) }; this.#render(); }
  set rows(value) { this.#rows = value || []; this.#render(); }
  set meta(value) { this.#meta = value || null; this.#render(); }
  set notice(value) { this.#notice = value || null; this.#render(); }

  get rows() { return this.#rows; }

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

  // ---- the channel picker ----------------------------------------------

  /**
   * The combo holds channel *names* and the filter holds channel *ids*, because
   * names are what a person reads and the endpoint keys on ids. So a pick is
   * resolved by name against the list the envelope published.
   *
   * Anything that is not a channel name is not a query — half-typed text on blur,
   * a paste from somewhere else. Rather than filter down to nothing, the control
   * puts back what it was showing and changes nothing.
   */
  #picked(value, isId = false) {
    const channels = this.#meta?.channels || [];
    const hit = isId
      ? channels.find((channel) => channel.id === value)
      : channels.find((channel) => channel.name === value.trim());

    if (!hit && value) {
      this.#syncPicker();
      return;
    }

    const channel = hit ? hit.id : null;
    if (channel !== this.#filter.channel) this.#request({ channel });
  }

  /**
   * Write the current filter into the control. Skipped while the combo has
   * focus: renders happen on every load, and one of them landing mid-word would
   * delete what was being typed.
   */
  #syncPicker() {
    const current = (this.#meta?.channels || [])
      .find((channel) => channel.id === this.#filter.channel);

    if (this.#fallback) {
      this.#fallback.value = current ? current.id : '';
      return;
    }
    if (this.shadowRoot.activeElement !== this.#combo) {
      this.#combo.value = current ? current.name : '';
    }
  }

  #renderPicker() {
    const channels = this.#meta?.channels || [];

    if (this.#fallback) {
      this.#fallback.textContent = '';
      this.#fallback.append(el('option', null, 'all channels'));
      for (const channel of channels) {
        const option = el('option', null, channel.name);
        option.value = channel.id;
        this.#fallback.append(option);
      }
    } else {
      this.#datalist.textContent = '';
      for (const channel of channels) {
        const option = el('option');
        option.value = channel.name;
        this.#datalist.append(option);
      }
    }

    this.#syncPicker();
    const control = this.#fallback || this.#combo;
    control.disabled = !this.#meta;
  }

  // ---- render ----------------------------------------------------------

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

      const title = row.display_title || (row.title ? sentenceCase(row.title) : row.video_id);
      const heading = el('span', 'row-heading');
      const recommendation = row.watch_recommendation;
      const language = row.lang?.toLowerCase().startsWith('fr') ? 'fr' : 'en';
      const labels = RECOMMENDATION_LABELS[language];
      const label = Object.hasOwn(labels, recommendation) ? labels[recommendation] : null;
      if (label) {
        const dot = el('span', 'recommendation-dot');
        dot.dataset.recommendation = recommendation;
        dot.title = label;
        dot.setAttribute('role', 'img');
        dot.setAttribute('aria-label', label);
        heading.append(dot);
      }
      heading.append(el('span', 'title', title));
      button.append(heading);

      // No status, no character count, and no caption kind. The kind used to be
      // here too, but the sidebar carries no transcript, so "auto" beside a
      // video title is a caption-source fact with nothing to do with the list —
      // it belongs with the transcript it describes. What is left is who and
      // when; the title above is the third thing that identifies a row.
      const meta = el('span', 'meta');
      meta.append(el('span', null, row.channel_name || row.channel_id));
      const when = el('span', null, relativeDay(row.published_at));
      when.title = row.published_at || '';
      meta.append(when);
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
    this.#renderPicker();
    this.#renderRows();
    this.#renderPager();
  }
}

customElements.define('tessera-video-list', TesseraVideoList);
