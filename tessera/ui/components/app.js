import { sheet } from '../styles.js';
import { isVideoId } from './video-player.js';
import { OK } from './status.js';
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
  // Rows with no transcript are hidden by default: 9 of 23 today, so an
  // unfiltered chronological list is mostly dead ends. A `status` parameter
  // rather than a client-side display filter, because hiding rows here would
  // leave `meta.total` reporting 23 while 14 were on screen — the specific lie
  // the envelope exists to prevent.
  #state = { channel: null, status: OK, offset: 0 };

  #list;
  #player;
  #transcript;

  #selected = null;
  #detail = null;
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