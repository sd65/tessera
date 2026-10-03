const OVERRIDE_KEY = 'tessera-theme-override';
const NIGHT_START = 20 * 60 + 30;
const MORNING_START = 7 * 60;

/** The scheduled theme in the user's local time. */
export function scheduledTheme(date = new Date()) {
  const minutes = date.getHours() * 60 + date.getMinutes();
  return minutes >= NIGHT_START || minutes < MORNING_START ? 'dark' : 'light';
}

/** The next automatic light/dark boundary, expressed in local time. */
export function nextThemeBoundary(date = new Date()) {
  const minutes = date.getHours() * 60 + date.getMinutes();
  if (minutes < MORNING_START) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 7, 0);
  }
  if (minutes < NIGHT_START) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 20, 30);
  }
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1, 7, 0);
}

function readOverride(storage, now) {
  if (!storage) return null;
  try {
    const saved = JSON.parse(storage.getItem(OVERRIDE_KEY));
    if (
      (saved?.theme === 'light' || saved?.theme === 'dark')
      && Number.isFinite(saved.until)
      && saved.until > now.getTime()
    ) {
      return saved.theme;
    }
    storage.removeItem(OVERRIDE_KEY);
  } catch {
    try { storage.removeItem(OVERRIDE_KEY); } catch { /* Storage may be disabled. */ }
  }
  return null;
}

/** Install the sun-only control; manual choices last until the next schedule edge. */
export function installThemeToggle(button) {
  const root = document.documentElement;
  let storage = null;
  try { storage = window.localStorage; } catch { /* Private browsing can deny storage. */ }

  let timer = null;

  function apply(theme) {
    root.dataset.theme = theme;
    const label = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
    button.setAttribute('aria-pressed', String(theme === 'dark'));
  }

  function refresh() {
    const now = new Date();
    apply(readOverride(storage, now) || scheduledTheme(now));
    scheduleRefresh(now);
  }

  function scheduleRefresh(now) {
    if (timer !== null) window.clearTimeout(timer);
    timer = window.setTimeout(refresh, Math.max(1, nextThemeBoundary(now).getTime() - now.getTime()));
  }

  function toggle() {
    const now = new Date();
    const theme = (root.dataset.theme || scheduledTheme(now)) === 'dark' ? 'light' : 'dark';
    if (storage) {
      try {
        storage.setItem(OVERRIDE_KEY, JSON.stringify({
          theme,
          until: nextThemeBoundary(now).getTime(),
        }));
      } catch { /* The manual choice still applies until the next boundary. */ }
    }
    apply(theme);
    scheduleRefresh(now);
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') refresh();
  }

  function onStorage(event) {
    if (event.key === OVERRIDE_KEY) refresh();
  }

  button.addEventListener('click', toggle);
  document.addEventListener('visibilitychange', onVisibilityChange);
  window.addEventListener('storage', onStorage);
  refresh();

  return () => {
    button.removeEventListener('click', toggle);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    window.removeEventListener('storage', onStorage);
    if (timer !== null) window.clearTimeout(timer);
  };
}
