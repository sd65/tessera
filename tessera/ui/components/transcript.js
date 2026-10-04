import { sheet } from '../styles.js';
import { el } from './el.js';
import { findTranscriptMatches } from '../transcript-search.js';

/**
 * Summary and quote-verified passages, with a searchable transcript drawer.
 *
 * The summary is model output, so it is parsed into a small, fixed set of
 * elements — numbered section headings, paragraphs, bullet lists, `<strong>` —
 * and every piece of text goes through `textContent`. Nothing the model writes
 * is ever interpolated as HTML or CSS, which is what keeps the page's CSP (no
 * inline script, no inline style) intact.
 *
 * The transcript is the *normalised* text: the same string the model was fed,
 * so a quote in the summary is searchable in what the reader expands. It arrives
 * as one unbroken single-spaced run and is rendered as received. The drawer adds
 * literal, case-insensitive search with highlighted, keyboard-navigable matches.
 * A transcript with no summary yet still shows under the summary-unavailable note.
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
function renderSummary(text, passages, videoId, languageCode) {
  const frag = document.createDocumentFragment();
  let list = null;
  let sectionNumber = null;

  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) {
      list = null;
      continue;
    }

    const heading = trimmed.match(HEADING);
    if (heading) {
      list = null;
      sectionNumber = heading[1];
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
    if (sectionNumber === '5' && /^b\)\s*/iu.test(trimmed)) {
      const recommendation = el('div', 'watch-recommendation');
      recommendation.append(paragraph);
      const passageList = buildPassages(passages, videoId, languageCode);
      if (passageList) recommendation.append(passageList);
      frag.append(recommendation);
    } else {
      frag.append(paragraph);
    }
  }
  return frag;
}

function language(code) {
  return typeof code === 'string' && code.toLowerCase().split(/[-_]/, 1)[0] === 'fr'
    ? 'fr'
    : 'en';
}

const COPY = {
  en: {
    unavailable: 'duration unavailable',
    transcript: 'Transcript',
    search: 'Search transcript…',
    previous: 'Previous match',
    next: 'Next match',
    close: 'Close transcript',
    noMatches: 'No matches',
  },
  fr: {
    unavailable: 'durée indisponible',
    transcript: 'Transcription',
    search: 'Rechercher dans la transcription…',
    previous: 'Occurrence précédente',
    next: 'Occurrence suivante',
    close: 'Fermer la transcription',
    noMatches: 'Aucun résultat',
  },
};

function buildTranscript(text, languageCode) {
  const copy = COPY[language(languageCode)];
  const panel = el('section', 'transcript-panel');
  const toggle = el('button', 'transcript-toggle', copy.transcript);
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');

  const expanded = el('div', 'transcript-open');
  expanded.hidden = true;
  const toolbar = el('div', 'transcript-toolbar');
  toolbar.append(el('strong', 'transcript-title', copy.transcript));
  const close = el('button', 'transcript-close', '×');
  close.type = 'button';
  close.setAttribute('aria-label', copy.close);
  toolbar.append(close);

  const searchbar = el('div', 'transcript-searchbar');
  const input = document.createElement('input');
  input.type = 'search';
  input.className = 'transcript-search';
  input.placeholder = copy.search;
  input.setAttribute('aria-label', copy.search);
  const count = el('output', 'transcript-count');
  count.setAttribute('aria-live', 'polite');
  const previous = el('button', 'transcript-nav', '‹');
  previous.type = 'button';
  previous.setAttribute('aria-label', copy.previous);
  const next = el('button', 'transcript-nav', '›');
  next.type = 'button';
  next.setAttribute('aria-label', copy.next);
  searchbar.append(input, count, previous, next);

  const textBox = el('div', 'transcript-text', text);
  textBox.tabIndex = 0;
  textBox.setAttribute('role', 'region');
  textBox.setAttribute('aria-label', copy.transcript);
  expanded.append(toolbar, searchbar, textBox);
  panel.append(toggle, expanded);

  let matches = [];
  let activeIndex = -1;

  const syncActive = (scroll = false) => {
    matches.forEach((mark, index) => {
      mark.classList.toggle('active', index === activeIndex);
    });
    count.textContent = matches.length
      ? `${activeIndex + 1} / ${matches.length}`
      : (input.value ? copy.noMatches : '');
    previous.disabled = next.disabled = matches.length < 2;
    if (scroll && activeIndex >= 0) {
      matches[activeIndex].scrollIntoView({ block: 'center' });
    }
  };

  const search = () => {
    const query = input.value;
    matches = [];
    activeIndex = -1;
    if (!query) {
      textBox.textContent = text;
      syncActive();
      return;
    }

    const ranges = findTranscriptMatches(text, query);
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    for (const range of ranges) {
      fragment.append(document.createTextNode(text.slice(cursor, range.start)));
      const mark = el('mark', 'match', text.slice(range.start, range.end));
      matches.push(mark);
      fragment.append(mark);
      cursor = range.end;
    }
    if (!matches.length) {
      textBox.textContent = text;
      syncActive();
      return;
    }
    fragment.append(document.createTextNode(text.slice(cursor)));
    textBox.replaceChildren(fragment);
    activeIndex = 0;
    syncActive(true);
  };

  const step = (direction) => {
    if (!matches.length) return;
    activeIndex = (activeIndex + direction + matches.length) % matches.length;
    syncActive(true);
  };

  const setOpen = (open) => {
    panel.classList.toggle('open', open);
    toggle.hidden = open;
    toggle.setAttribute('aria-expanded', String(open));
    expanded.hidden = !open;
    if (open) input.focus();
    else toggle.focus();
  };

  toggle.addEventListener('click', () => setOpen(true));
  close.addEventListener('click', () => setOpen(false));
  input.addEventListener('input', search);
  previous.addEventListener('click', () => step(-1));
  next.addEventListener('click', () => step(1));
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.isComposing) {
      event.preventDefault();
      step(event.shiftKey ? -1 : 1);
    }
  });
  return panel;
}

function timestamp(startMs) {
  const seconds = Math.floor(startMs / 1000);
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes >= 60) {
    return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
  }
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}

function watchDuration(durationMs) {
  return timestamp(Math.ceil(durationMs / 1000) * 1000);
}

function buildPassages(passages, videoId, languageCode) {
  const copy = COPY[language(languageCode)];
  const valid = Array.isArray(passages)
    ? passages.filter((item) => item
      && typeof item.label === 'string'
      && typeof item.quote === 'string'
      && Number.isSafeInteger(item.start_ms)
      && item.start_ms >= 0)
    : [];
  if (!valid.length) return null;

  const section = el('div', 'passages');
  const list = document.createElement('ul');
  for (const item of valid) {
    const row = document.createElement('li');
    const button = el('button', 'passage', '');
    button.type = 'button';
    const hasDuration = Number.isSafeInteger(item.duration_ms) && item.duration_ms > 0;
    const watchTime = hasDuration
      ? `${timestamp(item.start_ms)} · ~${watchDuration(item.duration_ms)}`
      : `${timestamp(item.start_ms)} · ${copy.unavailable}`;
    button.append(
      el('span', 'passage-label', item.label),
      el('span', 'passage-quote', `« ${item.quote} »`),
      el('span', 'passage-time', watchTime),
    );
    button.setAttribute('aria-label', `${item.label}, ${watchTime}`);
    button.addEventListener('click', () => {
      button.dispatchEvent(new CustomEvent('seek-video', {
        detail: { videoId, startMs: item.start_ms },
        bubbles: true,
        composed: true,
      }));
    });
    row.append(button);
    list.append(row);
  }
  section.append(list);
  return section;
}

export class TesseraTranscript extends HTMLElement {
  #header;
  #headerMeta;
  #body;

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.append(TEMPLATE.content.cloneNode(true));

    this.#header = this.shadowRoot.querySelector('header');
    this.#header.replaceChildren(
      el('strong', 'info-title', 'Info'),
      el('span', 'info-meta'),
    );
    this.#headerMeta = this.#header.querySelector('.info-meta');
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
    this.#body.classList.remove('has-transcript');

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
      this.#headerMeta.textContent = summaryReady ? provenance : `${provenance}  ·  no summary`;
    } else {
      this.#headerMeta.textContent = `x${video.attempts ?? 0} attempts`;
    }

    if (summaryReady) {
      const box = el('div', 'summary-text');
      box.append(el('h2', 'info-title summary-title', 'Summary'));
      box.append(el('div', 'summary-copy'));
      box.querySelector('.summary-copy').append(renderSummary(
        video.summary,
        video.passages,
        video.video_id,
        video.lang,
      ));
      this.#body.append(box);
    } else if (hasTranscript) {
      // The transcript is readable but the model has not answered yet. Say so,
      // with the stored reason, and still offer the transcript below.
      const box = el('div', 'summary-text');
      box.append(el('h2', 'info-title summary-title', 'Summary'));
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
      this.#body.classList.add('has-transcript');
      this.#body.append(buildTranscript(video.normalized_text || video.text, video.lang));
    }
  }
}

customElements.define('tessera-transcript', TesseraTranscript);
