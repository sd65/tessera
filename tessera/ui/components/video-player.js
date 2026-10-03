import { sheet } from '../styles.js';
import { relativeDay, sentenceCase } from '../format.js';

/**
 * The nocookie embed, and the title/date line above it.
 *
 * The iframe is the one part of this page that talks to anything. It is
 * youtube-nocookie.com rather than youtube.com so the embed sets no cookies.
 *
 * `referrerpolicy` here is not decoration. YouTube refuses to configure its
 * player when the embed request carries no `Referer`, and the resulting page
 * says so: "Video player configuration error — Error 153". It was reproduced
 * here by driving Chrome over CDP, attaching to the embed's out-of-process
 * target and reading its DOM: with the document sending
 * `Referrer-Policy: no-referrer`, the iframe rendered exactly that error, and
 * with `origin-when-cross-origin` it rendered the video. So the page sends an
 * origin and nothing more — no path, no `?v=` — which is the least a working
 * embed will accept. The attribute repeats it on the element that needs it, so
 * the dependency survives the document header changing.
 *
 * Deliberately the plain embed rather than the IFrame Player API: the embed's
 * own controls already cover playback, and the API would be a script from
 * another origin that CSP would then have to allow.
 */

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <div class="player">
    <div class="frame">
      <iframe title="YouTube player"
              allow="encrypted-media; picture-in-picture"
              allowfullscreen
              referrerpolicy="origin-when-cross-origin"
              hidden></iframe>
      <div class="placeholder">No video selected</div>
    </div>
    <div class="caption" hidden>
      <div class="title"></div>
      <div class="meta"></div>
    </div>
  </div>
`;

// YouTube ids are 11 characters of URL-safe base64. Checked before the value is
// interpolated into the embed URL, because `?v=` is user input and the id
// reaches here without passing through the server's own validation.
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

export function isVideoId(value) {
  return typeof value === 'string' && VIDEO_ID.test(value);
}

export class TesseraVideoPlayer extends HTMLElement {
  #detail = null;
  #frame;
  #placeholder;
  #caption;
  #title;
  #meta;

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.append(TEMPLATE.content.cloneNode(true));

    this.#frame = this.shadowRoot.querySelector('iframe');
    this.#placeholder = this.shadowRoot.querySelector('.placeholder');
    this.#caption = this.shadowRoot.querySelector('.caption');
    this.#title = this.shadowRoot.querySelector('.title');
    this.#meta = this.shadowRoot.querySelector('.meta');
  }

  set detail(video) {
    this.#detail = video || null;
    this.#render();
  }

  #render() {
    const video = this.#detail;

    if (video && isVideoId(video.video_id)) {
      this.#frame.setAttribute('src', `https://www.youtube-nocookie.com/embed/${video.video_id}`);
      this.#frame.hidden = false;
      this.#placeholder.hidden = true;
    } else {
      this.#frame.removeAttribute('src');
      this.#frame.hidden = true;
      this.#placeholder.hidden = false;
    }

    if (!video) {
      this.#caption.hidden = true;
      return;
    }

    this.#caption.hidden = false;
    // textContent, not innerHTML: titles come from an RSS feed.
    this.#title.textContent = video.display_title
      || (video.title ? sentenceCase(video.title) : video.video_id);

    const when = relativeDay(video.published_at);
    this.#meta.textContent = [video.channel_name, when].filter(Boolean).join('  ·  ');
    // Relative everywhere, so the exact timestamp is a hover away. Same reason
    // as the row's date; the two are separate elements so only one needs it.
    this.#meta.title = video.published_at || '';
  }
}

customElements.define('tessera-video-player', TesseraVideoPlayer);
