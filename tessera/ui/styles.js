/**
 * One stylesheet for the whole page, constructed once and adopted everywhere:
 * onto `document` by ui/app.js for html/body, and onto every component's shadow
 * root by components/app.js.
 *
 * Shadow DOM is meant to isolate style, and adopting one sheet into all four
 * roots is a deliberate exception to that. The alternative is four near-copies
 * of the same palette and spacing values drifting apart, which is the failure
 * mode isolation is supposed to prevent rather than cause.
 *
 * Custom properties are defined on `:root` and inherit into shadow trees, so
 * this works across the boundary even though the selectors do not.
 */

const CSS = `
:root {
  color-scheme: light dark;

  --bg: #f7f6f4;
  --panel: #fffefc;
  --panel-2: #f1efeb;
  --line: #e0dcd5;
  --ink: #1b1a17;
  --muted: #6d6862;
  --accent: #2c6b4c;
  --accent-soft: #e4efe7;
  --ok: #2c6b4c;
  --retryable: #8a5a12;
  --permanent: #93342f;

  --radius: 6px;
  --gap: 12px;

  font-family: ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.45;
  color: var(--ink);
  background: var(--bg);
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17171a;
    --panel: #1e1e22;
    --panel-2: #26262b;
    --line: #33333a;
    --ink: #e8e6e1;
    --muted: #9b968e;
    --accent: #74c39b;
    --accent-soft: #22322a;
    --ok: #74c39b;
    --retryable: #d9a441;
    --permanent: #e08580;
  }
}

html, body {
  height: 100%;
  margin: 0;
}

* { box-sizing: border-box; }

button {
  font: inherit;
  color: inherit;
  cursor: pointer;
}

/* ---- <tessera-app> ---------------------------------------------------- */

tessera-app {
  display: block;
  height: 100dvh;
  overflow: hidden;
}

.shell {
  display: grid;
  grid-template-rows: auto 1fr;
  height: 100%;
  min-height: 0;
}

.masthead {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 10px var(--gap);
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}

.masthead h1 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0.02em;
}

.masthead .tag {
  color: var(--muted);
  font-size: 12px;
}

.columns {
  display: grid;
  grid-template-columns: minmax(260px, 340px) minmax(340px, 1fr) minmax(320px, 1.15fr);
  min-height: 0;
}

.columns > * {
  min-width: 0;
  min-height: 0;
}

/* The three pane hosts are grid children, so they are block-level and stretched
   to the row already — but stated, because the shadow content below depends on
   filling them. */
tessera-video-list,
tessera-video-player,
tessera-transcript {
  display: block;
  height: 100%;
  overflow: hidden;
}

/* ---- <tessera-video-list> --------------------------------------------- */

.filter {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: var(--gap);
  border-bottom: 1px solid var(--line);
}

.channels {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.channels button {
  padding: 3px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel);
  font-size: 12px;
  color: var(--muted);
}

.channels button[aria-pressed="true"] {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}

.toggle {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--muted);
}

.rows {
  overflow-y: auto;
  flex: 1;
}

.row {
  display: block;
  width: 100%;
  text-align: left;
  padding: 9px var(--gap);
  border: 0;
  border-bottom: 1px solid var(--line);
  border-left: 3px solid transparent;
  background: none;
}

.row:hover { background: var(--panel-2); }

.row[aria-current="true"] {
  background: var(--accent-soft);
  border-left-color: var(--accent);
}

.row .title {
  display: block;
  font-size: 13px;
  font-weight: 550;
  line-height: 1.3;
  margin-bottom: 3px;
}

.row[aria-current="true"] .title { color: var(--accent); }

.row .meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 9px;
  font-size: 11px;
  color: var(--muted);
}

.row .status {
  font-weight: 650;
}

.row .status.is-ok { color: var(--ok); }
.row .status.is-retryable { color: var(--retryable); }
.row .status.is-permanent { color: var(--permanent); }

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px var(--gap);
  border-top: 1px solid var(--line);
  background: var(--panel);
  font-size: 12px;
  color: var(--muted);
}

.pager button {
  padding: 3px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}

.pager button:disabled { opacity: 0.4; cursor: default; }

.notice {
  padding: 18px var(--gap);
  color: var(--muted);
  font-size: 13px;
}

/* ---- <tessera-video-player> ------------------------------------------ */

.player {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.player .frame {
  position: relative;
  flex: 0 0 auto;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
}

.player .frame iframe {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
}

.player .placeholder {
  position: absolute;
  inset: 0;
  display: grid;
  place-content: center;
  color: var(--muted);
  font-size: 13px;
}

.player .caption {
  padding: 10px var(--gap);
  border-bottom: 1px solid var(--line);
}

.player .caption .title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
  margin-bottom: 3px;
}

.player .caption .meta {
  font-size: 11px;
  color: var(--muted);
}

/* ---- <tessera-transcript> -------------------------------------------- */

.pane {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.pane > header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px var(--gap);
  border-bottom: 1px solid var(--line);
  background: var(--panel);
  font-size: 12px;
  color: var(--muted);
}

/* The one region that reflows prose. A 134KB single-spaced string renders as
   one text node without trouble, so there is no chunking and no
   virtualisation here — just a comfortable measure and a scrollbar. */
.text {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px var(--gap) 48px;
  max-width: 68ch;
  line-height: 1.62;
  white-space: pre-wrap;
  overflow-wrap: break-word;
}

.empty {
  padding: 18px var(--gap);
  color: var(--muted);
  font-size: 13px;
}

.reason {
  padding: 18px var(--gap);
  font-size: 13px;
}

.reason .headline {
  font-weight: 600;
  margin-bottom: 6px;
}

.reason .detail {
  color: var(--muted);
}
`;

export const sheet = new CSSStyleSheet();
sheet.replaceSync(CSS);