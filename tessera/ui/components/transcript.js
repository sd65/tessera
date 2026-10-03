import { sheet } from '../styles.js';
import { el } from './el.js';

/**
 * The summary first, and the full transcript behind a disclosure underneath.
 *
 * The summary is model output, so it is parsed into a small, fixed set of
 * elements — numbered section headings, paragraphs, bullet lists, `<strong>` —
 * and every piece of text goes through `textContent`. Nothing the model writes
 * is ever interpolated as HTML or CSS, which is what keeps the page's CSP (no
 * inline script, no inline style) intact.
 *
 * The transcript is the *normalised* text: the same string the model was fed,
 * so a quote in the summary is searchable in what the reader expands. It arrives
 * as one unbroken single-spaced run and is rendered as received, as real DOM
 * text — which is why browser find-in-page works and there is no search box. A
 * transcript with no summary yet still shows here, under the note that says so.
 */

// The literal the fetcher writes into a good row's `status`, matching `OK` in
// transcript.py and summarize.py.
const OK = 'ok';

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <div class="pane">
    <header></header>
    <div class="body"></div>
  </div>
`;

// A numbered section heading, the shape `prompt` asks for: "1. SUBJECT",
// "**4. KEY TAKEAWAYS**". Matched by shape rather than by a list of the current
// sections, so editing the prompt does not mean editing the UI.
const HEADING = /^(?:\*\*)?\s*(\d{1,2})\.\s*(\p{Lu}[\p{Lu}\p{N} '&/,.-]{1,40}?)\s*(?:\*\*)?:?\s*$/u;
const BULLET = /^\s*[-*\u2022]\s+(.*)$/;
const BOLD = /\*\*(.+?)\*\*/g;

/** Split one line on `**bold**`, returning text nodes and `<strong>`s. */
function inline(text) {
  const nodes = [];
  let last = 0;
  let match;
  BOLD.lastIndex = 0;
  while ((match = BOLD.exec(text)) !== null) {
    if (match.index > last) nodes.push(document.createTextNode(text.slice(last, match.index)));
    const strong = document.createElement('strong');
    strong.textContent = match[1];
    nodes.push(strong);
    last = BOLD.lastIndex;
  }
  if (last < text.length) nodes.push(document.createTextNode(text.slice(last)));
  return nodes;
}

/** Turn the model's answer into nodes. A deliberately small Markdown subset. */
function renderSummary(text) {
  const frag = document.createDocumentFragment();
  let list = null;

  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) {
      list = null;
      continue;
    }

    const heading = trimmed.match(HEADING);
    if (heading) {
      list = null;
      frag.append(el('h3', 'section', `${heading[1]}. ${heading[2]}`));
      continue;
    }

    const bullet = trimmed.match(BULLET);
    if (bullet) {
      if (!list) {
        list = document.createElement('ul');
        frag.append(list);
      }
      const item = document.createElement('li');
      item.append(...inline(bullet[1]));
      list.append(item);
      continue;
    }

    list = null;
    const paragraph = document.createElement('p');
    paragraph.append(...inline(trimmed));
    frag.append(paragraph);
  }
  return frag;
}

/** The collapsed-by-default transcript disclosure. */
function buildTranscript(text) {
  const details = document.createElement('details');
  details.className = 'transcript';
  const toggle = document.createElement('summary');
  toggle.textContent = 'Transcript';
  details.append(toggle, el('div', 'text', text));
  return details;
}

export class TesseraTranscript extends HTMLElement {
  #header;
  #body;

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.append(TEMPLATE.content.cloneNode(true));

    this.#header = this.shadowRoot.querySelector('header');
    this.#body = this.shadowRoot.querySelector('.body');
  }

  set detail(video) {
    this.#detail = video || null;
    this.#render();
  }

  #detail = null;

  #render() {
    const video = this.#detail;
    this.#body.textContent = '';

    if (!video) {
      this.#header.hidden = true;
      this.#body.append(el('p', 'empty', 'Select a video.'));
      return;
    }

    const hasTranscript = Boolean(video.text);
    const summaryReady = video.summary_status === OK && Boolean(video.summary);

    // Provenance as before, plus the summary state. The count of attempts is
    // the useful line when there is no transcript; the caption kind is a fact
    // about the transcript and belongs here rather than on the list row.
    this.#header.hidden = false;
    if (video.status === OK) {
      const provenance = [video.lang, video.kind].filter(Boolean).join('  ·  ');
      this.#header.textContent = summaryReady ? provenance : `${provenance}  ·  no summary`;
    } else {
      this.#header.textContent = `x${video.attempts ?? 0} attempts`;
    }

    if (summaryReady) {
      const box = el('div', 'summary-text');
      box.append(renderSummary(video.summary));
      this.#body.append(box);
    } else if (hasTranscript) {
      // The transcript is readable but the model has not answered yet. Say so,
      // with the stored reason, and still offer the transcript below.
      const box = el('div', 'summary-text');
      const note = el('div', 'notice');
      note.append(el('div', 'headline', 'Summary unavailable'));
      note.append(el('div', 'detail',
        video.summary_error || 'no summary stored yet — the next fetch will produce one'));
      box.append(note);
      this.#body.append(box);
    } else {
      // No transcript at all: show the stored reason. A failed row is a real
      // state with an explanation attached, not an empty pane.
      const reason = el('div', 'reason');
      reason.append(el('div', 'headline', video.status || 'no transcript'));
      reason.append(el('div', 'detail', video.error || 'no stored reason for this outcome'));
      this.#body.append(reason);
      return;
    }

    if (hasTranscript) {
      this.#body.append(buildTranscript(video.normalized_text || video.text));
    }
  }
}

customElements.define('tessera-transcript', TesseraTranscript);
