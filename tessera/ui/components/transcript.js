import { sheet } from '../styles.js';
import { el } from './el.js';

/**
 * The stored transcript, or the stored reason there isn't one.
 *
 * `text` arrives as one unbroken single-spaced string — `clean_captions`
 * finishes with `_SPACES.sub(" ", raw)`, so every line break in the caption
 * feed is already collapsed — and it is rendered as received. No paragraphing,
 * no sentence splitting, no chunking: the longest row in the corpus is 134,632
 * characters and it renders as a single text node without trouble.
 *
 * One useful consequence of leaving it as real DOM text: browser find-in-page
 * works, which is why there is no search box.
 */

// The literal the fetcher writes into a good row's `status`, matching `OK` in
// transcript.py. This used to live in a shared status.js with two other
// importers; it is a string, there is one copy of it left, and a file whose only
// content is that string is a file to keep in sync for nothing.
const OK = 'ok';

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <div class="pane">
    <header class="summary"></header>
    <div class="body"></div>
  </div>
`;

export class TesseraTranscript extends HTMLElement {
  #summary;
  #body;

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.append(TEMPLATE.content.cloneNode(true));

    this.#summary = this.shadowRoot.querySelector('.summary');
    this.#body = this.shadowRoot.querySelector('.body');
  }

  set detail(video) {
    this.#detail = video || null;
    this.#render();
  }

  #detail = null;

  #render() {
    const video = this.#detail;
    this.#summary.textContent = '';
    this.#body.textContent = '';

    if (!video) {
      this.#summary.hidden = true;
      this.#body.append(el('p', 'empty', 'Select a video.'));
      return;
    }

    // Keyed on status. This used to branch on `video.chars == null`, which was a
    // proxy that lied in both directions — 0 rather than NULL on a failed row,
    // and a stale count if a transcript were ever refetched with different text.
    // Provenance on the ok path; the attempt count on the other, because the
    // reason block underneath already names the status and the error.
    //
    // The caption kind lives here and not on the row: "auto" is a fact about
    // the transcript, and the sidebar does not have one to attach it to.
    this.#summary.hidden = false;
    this.#summary.textContent = video.status === OK
      ? [video.lang, video.kind].filter(Boolean).join('  ·  ')
      : `x${video.attempts ?? 0} attempts`;

    if (video.text) {
      this.#body.append(el('div', 'text', video.text));
      return;
    }

    // No transcript: show the stored reason. A failed row is a real state with
    // an explanation attached, not an empty pane — and it is a better view of
    // that state than `tessera status`, because the reason was truncated there.
    const reason = el('div', 'reason');
    reason.append(el('div', 'headline', video.status || 'no transcript'));
    reason.append(el('div', 'detail', video.error || 'no stored reason for this outcome'));
    this.#body.append(reason);
  }
}

customElements.define('tessera-transcript', TesseraTranscript);