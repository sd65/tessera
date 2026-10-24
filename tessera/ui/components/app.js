import { sheet } from '../styles.js';
import { installThemeToggle } from '../theme.js';
import { isVideoId } from './video-player.js';
import { el } from './el.js';
import './video-list.js';
import './transcript.js';

/**
 * State owner, and the only thing here that talks to the network.
 *
 * Owns the whole query — channel, offset, and the constant transcript filter —
 * and rebuilds it from the `filter-change` the list emits. Components receive
 * data and emit intent; they never fetch. The API endpoints called from this
 * file are the complete set of requests the page can make. Corpus reads and
 * chat forwarding stay centralized here rather than spread across components.
 *
 * Every load is stamped with a token. Clicking quickly through videos can leave
 * a detail request in flight, and without the check the slower earlier response
 * would win and put the wrong transcript on screen.
 */

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <div class="shell">
    <header class="masthead">
      <div class="brand">
        <img src="/favicon.svg" alt="" aria-hidden="true">
        <h1>Tessera</h1>
      </div>
      <div class="header-actions">
        <button class="theme-toggle" type="button" aria-label="Switch to dark theme" title="Switch to dark theme" aria-pressed="false">
          <span aria-hidden="true">☀</span>
        </button>
        <div class="health-wrap">
          <button class="health-indicator" type="button" aria-label="Database status: loading" aria-describedby="health-tooltip" data-state="loading">
            <span class="health-dot" aria-hidden="true"></span>
          </button>
          <div class="health-tooltip" id="health-tooltip" role="tooltip" hidden></div>
        </div>
      </div>
    </header>
    <div class="columns">
      <tessera-video-list></tessera-video-list>
      <tessera-video-player></tessera-video-player>
      <tessera-transcript></tessera-transcript>
    </div>
  </div>
`;

// The literal the fetcher writes into a good row's `status`, matching `OK` in
// transcript.py. Deliberately a second copy of the one in transcript.js: a
// shared module for a single string was tried and removed, and a query
// parameter is not a good enough reason to bring it back. If the two ever
// disagree, the sidebar and the reason pane disagree about what a transcript
// is, which is visible immediately rather than subtle.
const OK = 'ok';

// How long a summary must stay on screen before it counts as read. Not zero and
// not "on click": clicking through the sidebar is fast, and a threshold that low
// would mark read exactly the videos nobody looked at. One second is above the
// time it takes to move the pointer to the next row and click it, so a skim
// through the list still marks nothing — but only just, and a deliberate pause
// on a row before moving on is now enough to count as read.
const READ_AFTER_MS = 1_000;

export class TesseraApp extends HTMLElement {
  // Transcripts only. A video with no transcript does not get a row: the sidebar
  // is an index of what you can read, and the failure state those videos carry
  // belongs to a dedicated pane. So the query this owns is only the two things a
  // person can change; the status is a constant, sent by #load, not a control.
  #state = { channel: null, offset: 0 };

  #list;
  #player;
  #transcript;
  #themeToggle;
  #healthIndicator;
  #healthTooltip;
  #healthTimer;

  #selected = null;
  #detail = null;
  #started = false;
  #listToken = 0;
  #detailToken = 0;
  #chatByVideo = new Map();

  // Which summaries the reader has been through, as video ids. The server is
  // the source of truth — this exists so the sidebar can paint before the write
  // round-trip comes back, and so a failed write can be rolled back without a
  // refetch. Replaced wholesale from each list response.
  #read = new Set();

  // Read timer. Only one is ever pending, and it is always about #summaryVideo:
  // a second summary view cancels the first rather than queueing behind it.
  #readTimer = null;
  #summaryVideo = null;
  #readRemaining = READ_AFTER_MS;
  #readDeadline = 0;

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.append(TEMPLATE.content.cloneNode(true));

    this.#list = this.shadowRoot.querySelector('tessera-video-list');
    this.#player = this.shadowRoot.querySelector('tessera-video-player');
    this.#transcript = this.shadowRoot.querySelector('tessera-transcript');
    this.#themeToggle = this.shadowRoot.querySelector('.theme-toggle');
    this.#healthIndicator = this.shadowRoot.querySelector('.health-indicator');
    this.#healthTooltip = this.shadowRoot.querySelector('.health-tooltip');
  }

  async connectedCallback() {
    if (this.#started) return;
    this.#started = true;

    installThemeToggle(this.#themeToggle);
    await this.#loadHealth();
    this.#healthTimer = setInterval(() => this.#loadHealth(), 60_000);
    window.addEventListener('focus', this.#refreshHealthOnFocus);
    this.#list.addEventListener('select', (event) => this.#select(event.detail.videoId));
    this.#list.addEventListener('filter-change', (event) => this.#filter(event.detail));
    this.#list.addEventListener('read-toggle', (event) => {
      this.#markRead(event.detail.videoId, event.detail.read);
    });
    this.#list.addEventListener('mark-all-read', () => this.#markAllRead());
    this.#transcript.addEventListener('summary-view', (event) => {
      this.#startReadTimer(event.detail.videoId);
    });
    document.addEventListener('visibilitychange', this.#onVisibility);
    this.#player.addEventListener('chat-ask', (event) => this.#ask(event.detail.question));
    const seekVideo = (event) => {
      const { videoId, startMs } = event.detail || {};
      if (videoId === this.#selected) this.#player.seekTo(videoId, startMs);
    };
    this.#transcript.addEventListener('seek-video', seekVideo);
    this.#player.addEventListener('seek-video', seekVideo);

    const deepLink = new URLSearchParams(location.search).get('v');
    await this.#load();

    if (isVideoId(deepLink)) {
      await this.#select(deepLink);
    } else if (this.#list.rows.length) {
      // Open on something rather than on two empty panes.
      await this.#select(this.#list.rows[0].video_id);
    }
  }

  disconnectedCallback() {
    if (this.#healthTimer) clearInterval(this.#healthTimer);
    this.#stopReadTimer();
    window.removeEventListener('focus', this.#refreshHealthOnFocus);
    document.removeEventListener('visibilitychange', this.#onVisibility);
  }

  #refreshHealthOnFocus = () => this.#loadHealth();

  // ---- read tracking ----------------------------------------------------

  /**
   * Time only counts while the tab is visible.
   *
   * A tab left open overnight would otherwise mark every summary it ever showed
   * as read on a timer that ran while nobody was looking. The elapsed time is
   * measured against a deadline rather than restarted from zero, so a tab that
   * goes to the background 1.9 s in and comes straight back still marks read,
   * and one that comes back after an hour has lost all its progress.
   */
  #onVisibility = () => {
    if (document.visibilityState === 'hidden') this.#pauseReadTimer();
    else this.#resumeReadTimer();
  };

  #startReadTimer(videoId) {
    // A new summary supersedes the pending one. #select also cancels on every
    // selection, so this is the backstop for the case where a summary arrives
    // for a video that is no longer selected. A null id is the pane saying the
    // summary went away rather than a new one appearing.
    this.#stopReadTimer();
    if (!videoId) return;
    // Already read — by hand, or by an earlier visit. Either way there is nothing
    // to time, and re-timing it would rewrite the same row for no reason.
    if (this.#read.has(videoId)) return;
    this.#summaryVideo = videoId;
    this.#readRemaining = READ_AFTER_MS;
    this.#armReadTimer();
  }

  #armReadTimer() {
    this.#readDeadline = Date.now() + this.#readRemaining;
    this.#readTimer = setTimeout(() => {
      this.#readTimer = null;
      this.#readRemaining = 0;
      const videoId = this.#summaryVideo;
      this.#summaryVideo = null;
      if (!videoId) return;
      this.#markRead(videoId, true);
    }, this.#readRemaining);
  }

  #pauseReadTimer() {
    if (!this.#readTimer) return;
    clearTimeout(this.#readTimer);
    this.#readTimer = null;
    // Bank what was left. Clamped at zero so a clock that jumped forwards cannot
    // hand back a negative delay.
    this.#readRemaining = Math.max(0, this.#readDeadline - Date.now());
  }

  #resumeReadTimer() {
    // Nothing pending, or the tab was hidden with no summary open.
    const videoId = this.#summaryVideo;
    if (!videoId) return;
    // The delay elapsed while the tab was hidden. Marked now rather than
    // re-armed for zero, which would be the same call through a timer.
    if (this.#readRemaining <= 0) {
      this.#stopReadTimer();
      this.#markRead(videoId, true);
      return;
    }
    this.#armReadTimer();
  }

  #stopReadTimer() {
    this.#pauseReadTimer();
    this.#summaryVideo = null;
    this.#readRemaining = READ_AFTER_MS;
  }

  /**
   * Write one read marker, then paint it.
   *
   * Painted after the server confirms rather than optimistically. This was
   * written optimistic first and it is the wrong shape here for two reasons: a
   * failed write then has to be rolled back and reported, and the only surface
   * for reporting it is the list's `notice`, which replaces the rows themselves —
   * so one failed toggle would blank 200 titles to explain a single rejected
   * write. Waiting costs a few milliseconds against a loopback socket and a
   * sub-millisecond SQLite commit, and it makes the sidebar agree with the
   * database at every instant without needing an undo path.
   */
  async #markRead(videoId, read) {
    if (!isVideoId(videoId)) return;
    // Already in the state being asked for. The timer reaching this is normal —
    // a summary read by hand while its two seconds were still running.
    if (this.#read.has(videoId) === read) return;

    try {
      const response = await fetch('/api/read', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: videoId, read }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    } catch (error) {
      // Nothing to roll back — nothing was painted. A read marker that failed to
      // save stays unmarked, so the row is still bold and the next selection
      // will try again. Failing quietly here is the right trade: the video was
      // genuinely not recorded as read, and that is a state the user can see.
      console.warn(`Tessera: could not save the read marker for ${videoId}:`, error.message);
      return;
    }

    if (read) this.#read.add(videoId);
    else this.#read.delete(videoId);
    this.#list.read = this.#read;
  }

  async #markAllRead() {
    const channel = this.#state.channel;
    this.#list.readAllPending = true;
    try {
      const response = await fetch('/api/read/all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);

      // Reload only if the reader is still looking at the same channel. The
      // server owns the full filtered result set; a list response repaints the
      // visible page from committed read markers without a client-side copy of
      // every video in the corpus.
      if (this.#state.channel === channel) await this.#load();
    } catch (error) {
      console.warn('Tessera: could not mark filtered videos as read:', error.message);
    } finally {
      this.#list.readAllPending = false;
    }
  }

  async #loadHealth() {
    try {
      const response = await fetch('/api/doctor');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      this.#renderHealth(await response.json());
    } catch {
      this.#renderHealth(null);
    }
  }

  #renderHealth(health) {
    const state = health?.state || 'unknown';
    const labels = {
      ok: 'All clear',
      warning: 'Retryable work',
      error: 'Permanent errors',
      unknown: 'Status unavailable',
    };
    this.#healthIndicator.dataset.state = state;
    this.#healthIndicator.setAttribute('aria-label', `Database status: ${labels[state]}`);
    this.#healthTooltip.replaceChildren();
    this.#healthTooltip.hidden = false;
    this.#healthTooltip.append(el('strong', 'health-title', 'Database health'));

    if (!health) {
      this.#healthTooltip.append(el('div', 'health-line', 'Could not load database status.'));
      return;
    }

    const exhausted = health.exhausted_transcripts
      ? ` (${health.exhausted_transcripts} exhausted)`
      : '';
    const lines = [
      `Videos: ${health.videos}`,
      `Transcripts: ${health.transcripts} usable / ${health.transcript_records} records`,
      `Summaries: ${health.summaries}`,
      `Retryable transcript errors: ${health.retryable_transcripts}${exhausted}`,
      `Pending transcripts: ${health.pending_transcripts}`,
      `Failed summaries: ${health.failed_summaries}`,
      `Permanent errors: ${health.permanent_transcripts}`,
    ];
    for (const line of lines) {
      this.#healthTooltip.append(el('div', 'health-line', line));
    }
  }

  /**
 * Read the read set out of a list response.
 *
 * Built from the rows in hand rather than from a separate endpoint. The list is
 * the only query the sidebar makes and it pages, so this describes the rows the
 * user can see — which is all the sidebar can render anyway. Paging back to a
 * previous page re-reads that page's markers from the same response, so the
 * boldness never depends on a cache that a page change would invalidate.
 */
  #absorbRead(rows) {
    this.#read = new Set(
      rows.filter((row) => row.read_at).map((row) => row.video_id),
    );
    this.#list.read = this.#read;
  }

  async #load() {
    const token = ++this.#listToken;
    const query = new URLSearchParams();
    // Transcripts only. The server does the filtering, so `meta.total` and the
    // pager count what is actually shown; the list is never fetched wide and
    // thinned here.
    query.set('status', OK);
    if (this.#state.channel) query.set('channel', this.#state.channel);
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
      this.#absorbRead(envelope.rows);
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
      offset: next.offset ?? 0,
    };
    await this.#load();
  }

  async #select(videoId) {
    if (!isVideoId(videoId)) return;

    this.#selected = videoId;
    this.#list.selected = videoId;
    this.#syncChat();
    // Any pending timer was about the video just left behind. Cleared here
    // rather than waiting for the new summary's event, so a selection that never
    // loads a summary — a video with no transcript, a failed request — does not
    // leave the previous video's timer running to completion.
    this.#stopReadTimer();

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

  #conversation(videoId) {
    let state = this.#chatByVideo.get(videoId);
    if (!state) {
      state = { messages: [], busy: false, error: null };
      this.#chatByVideo.set(videoId, state);
    }
    return state;
  }

  #syncChat() {
    const state = this.#selected ? this.#conversation(this.#selected) : null;
    this.#player.chatView = {
      videoId: this.#selected,
      messages: state?.messages || [],
      busy: state?.busy || false,
      error: state?.error || null,
    };
  }

  async #ask(question) {
    const videoId = this.#selected;
    if (!videoId || typeof question !== 'string' || !question.trim()) return;
    const state = this.#conversation(videoId);
    if (state.busy) return;

    const history = state.messages.map(({ role, content }) => ({ role, content }));
    const messages = [...history, { role: 'user', content: question.trim() }];
    Object.assign(state, { messages, busy: true, error: null });
    if (videoId === this.#selected) this.#syncChat();

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: videoId, messages }),
        // Ollama has a 120-second server-side timeout. Leave a little room for
        // the local HTTP request to return its timeout error to the page.
        signal: AbortSignal.timeout(125_000),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      if (typeof payload.answer !== 'string' || !payload.answer.trim()) {
        throw new Error('The server returned an empty answer.');
      }
      state.messages = [...messages, {
        role: 'assistant',
        content: payload.answer,
        citations: Array.isArray(payload.citations) ? payload.citations : [],
      }];
    } catch (error) {
      state.error = `Could not get an answer: ${error.message}`;
    } finally {
      state.busy = false;
      if (videoId === this.#selected) this.#syncChat();
    }
  }
}

customElements.define('tessera-app', TesseraApp);
