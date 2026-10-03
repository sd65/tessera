import { sheet } from '../styles.js';

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

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <div class="pane">
    <header class="summary"></header>
    <div class="body"></div>
  </div>
`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

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

  get detail() {
    return this.#detail;
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

    this.#summary.hidden = false;
    this.#body.textContent = video.chars == null
      ? `x${video.attempts ?? 0} attempts`
      : [video.lang, video.kind, `${video.chars.toLocaleString()} chars`].filter(Boolean).join('  ·  ');

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