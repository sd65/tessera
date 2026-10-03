import { sheet } from '../styles.js';
import { isVideoId } from './video-player.js';
import './video-list.js';
import './transcript.js';

/**
 * State owner, and the only thing here that talks to the network.
 *
 * Owns the whole query — channel, status, offset — and rebuilds it from the
 * `filter-change` the list emits. Components receive data and emit intent; they
 * never fetch. The two endpoints called from this file are the complete set of
 * requests the page can make, which is what makes the read-only boundary a
 * property of the design rather than a rule people have to remember.
 *
 * Every load is stamped with a token. Clicking quickly through videos can leave
 * a detail request in flight, and without the check the slower earlier response
 * would win and put the wrong transcript on screen.
 */

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <div class="shell">
    <header class="masthead">
      <h1>tessera</h1>
      <span class="tag">read-only</span>
    </header>
    <div class="columns">
      <tessera-video-list></tessera-video-list>
      <tessera-video-player></tessera-video-player>
      <tessera-transcript></tessera-transcript>
    </div>
  </div>
`;

export class TesseraApp extends HTMLElement {
  #state = { channel: null, status: null, offset: 0 };

  #list;
  #player;
  #transcript;

  #selected = null;
  #detail = null;
  #statusDefaulted = false;
  #started = false;
  #listToken = 0;
  #detailToken = 0;

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.append(TEMPLATE.content.cloneNode(true));

    this.#list = this.shadowRoot.querySelector('tessera-video-list');
    this.#player = this.shadowRoot.querySelector('tessera-video-player');
    this.#transcript = this.shadowRoot.querySelector('tessera-transcript');
  }

  async connectedCallback() {
    if (this.#started) return;
    this.#started = true;

    this.#list.addEventListener('select', (event) => this.#select(event.detail.videoId));
    this.#list.addEventListener('filter-change', (event) => this.#filter(event.detail));

    const deepLink = new URLSearchParams(location.search).get('v');
    await this.#load();

    if (isVideoId(deepLink)) {
      await this.#select(deepLink);
    } else if (this.#list.rows.length) {
      // Open on something rather than on two empty panes.
      await this.#select(this.#list.rows[0].video_id);
    }
  }

  async #load() {
    const token = ++this.#listToken;
    const query = new URLSearchParams();
    if (this.#state.channel) query.set('channel', this.#state.channel);
    if (this.#state.status) query.set('status', this.#state.status);
    if (this.#state.offset) query.set('offset', String(this.#state.offset));
    const search = query.toString();

    this.#list.notice = 'Loading…';

    try {
      const res = await fetch(`/api/videos${search ? `?${search}` : ''}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const envelope = await res.json();
      if (token !== this.#listToken) return;

      if (!this.#statusDefaulted) {
        // Rows with no transcript are hidden on first load. 9 of 23 today, so
        // an unfiltered chronological list is mostly dead ends. Taken from
        // meta.ok rather than hardcoded here, like the rest of the taxonomy.
        this.#state.status = envelope.meta.ok;
        this.#statusDefaulted = true;

        // Which makes the first load necessarily two requests: the default is
        // only learnable from a response, so one is needed to read meta.ok and
        // another to apply it. Setting the state without re-requesting would
        // leave the list showing every row while claiming a filter it does not
        // have — and a total that agrees with neither. Guarded by the flag
        // above, so this can happen at most once per page load.
        if (this.#state.status !== query.get('status')) {
          await this.#load();
          return;
        }
      }

      this.#list.filter = this.#state;
      this.#list.meta = envelope.meta;
      this.#list.rows = envelope.rows;
      this.#list.selected = this.#selected;
      this.#list.notice = null;
    } catch (error) {
      if (token !== this.#listToken) return;
      this.#list.meta = null;
      this.#list.rows = [];
      this.#list.notice = `Could not load the corpus: ${error.message}`;
    }
  }

  async #filter(next) {
    this.#state = {
      channel: next.channel ?? null,
      status: next.status ?? null,
      offset: next.offset ?? 0,
    };
    await this.#load();
  }

  async #select(videoId) {
    if (!isVideoId(videoId)) return;

    this.#selected = videoId;
    this.#list.selected = videoId;

    if (!this.#detail || this.#detail.video_id !== videoId) {
      this.#detail = null;
      this.#player.detail = null;
      this.#transcript.detail = null;
    }

    // A transcript has no positions, so a video id is the only thing a URL can
    // carry. replaceState, not pushState: paging and selecting should not fill
    // the back button with history.
    history.replaceState(null, '', `?v=${videoId}`);

    const token = ++this.#detailToken;
    try {
      const res = await fetch(`/api/videos/${videoId}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      const detail = await res.json();
      if (token !== this.#detailToken) return;
      this.#detail = detail;
    } catch (error) {
      if (token !== this.#detailToken) return;
      this.#detail = {
        video_id: videoId,
        title: videoId,
        status: 'error',
        attempts: 0,
        error: `Could not load this video: ${error.message}`,
      };
    }

    this.#player.detail = this.#detail;
    this.#transcript.detail = this.#detail;
  }
}

customElements.define('tessera-app', TesseraApp);