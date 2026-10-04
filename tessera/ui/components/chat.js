import { sheet } from '../styles.js';
import { el } from './el.js';

const TEMPLATE = document.createElement('template');
TEMPLATE.innerHTML = `
  <section class="chat" aria-label="Ask questions about this video">
    <header>Video chat</header>
    <div class="chat-messages" role="log" aria-live="polite" aria-relevant="additions"></div>
    <div class="chat-error" role="alert" hidden></div>
    <form class="chat-form">
      <input type="text" aria-label="Your question" placeholder="Ask about this video…">
      <button type="submit">Ask</button>
    </form>
  </section>
`;

export class TesseraChat extends HTMLElement {
  #videoId = null;
  #messages = [];
  #busy = false;
  #error = null;
  #feed;
  #errorBox;
  #form;
  #input;
  #submit;

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.append(TEMPLATE.content.cloneNode(true));

    this.#feed = this.shadowRoot.querySelector('.chat-messages');
    this.#errorBox = this.shadowRoot.querySelector('.chat-error');
    this.#form = this.shadowRoot.querySelector('form');
    this.#input = this.shadowRoot.querySelector('input');
    this.#submit = this.shadowRoot.querySelector('button');

    this.#form.addEventListener('submit', (event) => {
      event.preventDefault();
      const question = this.#input.value.trim();
      if (!question || !this.#videoId || this.#busy) return;
      this.#input.value = '';
      this.dispatchEvent(new CustomEvent('chat-ask', {
        bubbles: true,
        composed: true,
        detail: { question },
      }));
    });
    this.#input.addEventListener('input', () => this.#syncForm());
  }

  set view(view) {
    this.#videoId = view?.videoId || null;
    this.#messages = Array.isArray(view?.messages) ? view.messages : [];
    this.#busy = Boolean(view?.busy);
    this.#error = view?.error || null;
    this.#render();
  }

  #render() {
    this.#feed.textContent = '';
    this.#feed.append(this.#message('assistant', 'Any questions?'));
    for (const message of this.#messages) {
      this.#feed.append(this.#message(message.role, message.content));
    }
    if (this.#busy) this.#feed.append(this.#message('assistant', 'Thinking…', true));
    this.#feed.scrollTop = this.#feed.scrollHeight;

    this.#errorBox.textContent = this.#error || '';
    this.#errorBox.hidden = !this.#error;
    this.#syncForm();
  }

  #syncForm() {
    this.#input.disabled = !this.#videoId || this.#busy;
    this.#submit.disabled = !this.#videoId || this.#busy || !this.#input.value.trim();
    this.#input.placeholder = this.#videoId ? 'Ask about this video…' : 'Select a video to chat';
  }

  #message(role, content, pending = false) {
    const item = el('div', `chat-message ${role}${pending ? ' pending' : ''}`);
    item.append(el('div', 'chat-role', role === 'user' ? 'You' : 'Assistant'));
    item.append(el('div', 'chat-content', content));
    return item;
  }
}

customElements.define('tessera-chat', TesseraChat);
