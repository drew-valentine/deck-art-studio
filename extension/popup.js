/**
 * Popup script for Deck Art Studio extension.
 * Handles import from studio, deck management, export, and settings.
 * Shared art import (file/URL) opens in a dedicated tab (import.html)
 * because Firefox closes popups when a file picker dialog opens.
 */

const $ = (sel) => document.querySelector(sel);
const setStatus = (id, msg, cls = 'info') => {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.className = `status ${cls}`;
};

// ── Stats ──────────────────────────────────────────────────────────────

async function refreshStats() {
  try {
    const resp = await browser.runtime.sendMessage({ type: 'get-stats' });
    $('#stats').textContent = `${resp.count} card${resp.count !== 1 ? 's' : ''} cached`;
  } catch {
    $('#stats').textContent = '0 cards cached';
  }
}

// ── Imported decks tracking ───────────────────────────────────────────

let currentActiveDeck = null; // null = all decks

async function recordImport(deckName, cardCount, uuids = []) {
  const { importedDecks = {} } = await browser.storage.local.get('importedDecks');
  // Merge UUIDs with any previously stored ones for this deck
  const existing = importedDecks[deckName]?.uuids || [];
  const merged = [...new Set([...existing, ...uuids])];
  importedDecks[deckName] = {
    cards: cardCount,
    uuids: merged,
    importedAt: Date.now(),
  };
  await browser.storage.local.set({ importedDecks });
  // Auto-activate the just-imported deck
  await setActiveDeck(deckName);
}

async function setActiveDeck(deckName) {
  currentActiveDeck = deckName;
  await browser.storage.local.set({ activeDeck: deckName });
  const { importedDecks = {} } = await browser.storage.local.get('importedDecks');
  renderImportedDecks(importedDecks);
  notifyContentScripts();
}

function renderImportedDecks(decks) {
  const container = $('#importedDecks');
  const entries = Object.entries(decks).sort((a, b) => b[1].importedAt - a[1].importedAt);
  if (entries.length === 0) {
    container.textContent = 'No decks imported yet.';
    return;
  }
  container.innerHTML = '';

  function makeDeckRow(name, metaText, isActive, onClick, onRemove) {
    const div = document.createElement('div');
    div.className = 'deck-item' + (isActive ? ' active' : '');
    div.style.cursor = 'pointer';
    div.addEventListener('click', onClick);

    const radio = document.createElement('span');
    radio.className = 'deck-radio';
    radio.textContent = isActive ? '\u25C9' : '\u25CB';
    div.appendChild(radio);

    const left = document.createElement('span');
    left.className = 'deck-name';
    left.textContent = name;
    div.appendChild(left);

    const meta = document.createElement('span');
    meta.className = 'deck-meta';
    meta.innerHTML = metaText;
    div.appendChild(meta);

    if (onRemove) {
      const btn = document.createElement('button');
      btn.className = 'danger';
      btn.style.cssText = 'padding:2px 6px;font-size:10px;margin-left:6px;flex-shrink:0;';
      btn.textContent = 'Remove';
      btn.addEventListener('click', (e) => { e.stopPropagation(); onRemove(); });
      div.appendChild(btn);
    }

    return div;
  }

  // "All decks" option
  container.appendChild(makeDeckRow(
    'All Decks', 'all imported art', currentActiveDeck === null,
    () => setActiveDeck(null), null
  ));

  for (const [name, info] of entries) {
    const ago = timeAgo(new Date(info.importedAt));
    container.appendChild(makeDeckRow(
      name, `${info.cards} cards &middot; ${ago}`, currentActiveDeck === name,
      () => setActiveDeck(name), () => removeDeck(name)
    ));
  }
}

async function removeDeck(deckName) {
  const { importedDecks = {} } = await browser.storage.local.get('importedDecks');
  if (!importedDecks[deckName]) return;

  // Delete this deck's cards from IndexedDB (by deck name)
  await browser.runtime.sendMessage({ type: 'delete-deck', deck: deckName });

  // Remove from import history
  delete importedDecks[deckName];
  await browser.storage.local.set({ importedDecks });

  // If removed deck was active, reset to all
  if (currentActiveDeck === deckName) {
    currentActiveDeck = null;
    await browser.storage.local.set({ activeDeck: null });
  }

  renderImportedDecks(importedDecks);
  refreshStats();
  notifyContentScripts();
}

function timeAgo(date) {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString();
}

async function loadImportedDecks() {
  const data = await browser.storage.local.get(['importedDecks', 'activeDeck']);
  currentActiveDeck = data.activeDeck ?? null;
  renderImportedDecks(data.importedDecks || {});
}

// ── Toggle art on/off ──────────────────────────────────────────────────

$('#toggleArt').addEventListener('change', async (e) => {
  const enabled = e.target.checked;
  // Store preference
  await browser.storage.local.set({ artEnabled: enabled });
  // Notify all edhplay tabs
  const tabs = await browser.tabs.query({ url: '*://edhplay.com/*' });
  for (const tab of tabs) {
    browser.tabs.sendMessage(tab.id, { type: 'toggle-art', enabled }).catch(() => {});
  }
});

// Load saved toggle state and studio URL, then auto-load decks
browser.storage.local.get(['artEnabled', 'studioUrl']).then(({ artEnabled, studioUrl }) => {
  $('#toggleArt').checked = artEnabled !== false; // default on
  if (studioUrl) $('#studioUrl').value = studioUrl;
  // Now that saved URL is restored, auto-load decks
  loadDecks();
});

// ── Import from Deck Art Studio ────────────────────────────────────────

// Persist URL changes
$('#studioUrl').addEventListener('change', () => {
  browser.storage.local.set({ studioUrl: $('#studioUrl').value.trim() });
});

$('#loadDecksBtn').addEventListener('click', loadDecks);
$('#deckSelect').addEventListener('change', () => {
  $('#importStudioBtn').disabled = !$('#deckSelect').value;
});

async function loadDecks() {
  const baseUrl = $('#studioUrl').value.replace(/\/+$/, '');
  setStatus('studioStatus', 'Loading decks...', 'info');
  try {
    const resp = await browser.runtime.sendMessage({
      type: 'list-decks',
      url: `${baseUrl}/api/decks`,
    });
    if (!resp.success) throw new Error(resp.error);
    const decks = resp.decks || [];

    const select = $('#deckSelect');
    select.innerHTML = '<option value="">Select a deck...</option>';
    for (const deck of decks) {
      const opt = document.createElement('option');
      opt.value = deck.id;
      opt.textContent = deck.name || deck.id;
      if (deck.is_active) opt.selected = true;
      select.appendChild(opt);
    }
    $('#importStudioBtn').disabled = !select.value;
    setStatus('studioStatus', `Found ${decks.length} deck(s)`, 'success');
  } catch (e) {
    setStatus('studioStatus', `Failed: ${e.message}`, 'error');
  }
}

$('#importStudioBtn').addEventListener('click', async () => {
  const baseUrl = $('#studioUrl').value.replace(/\/+$/, '');
  const deckId = $('#deckSelect').value;
  if (!deckId) return;

  setStatus('studioStatus', 'Fetching manifest...', 'info');
  $('#importStudioBtn').disabled = true;

  try {
    const url = `${baseUrl}/api/decks/${deckId}/export-manifest`;
    const resp = await browser.runtime.sendMessage({
      type: 'fetch-manifest',
      url,
      source: 'local',
    });
    if (!resp.success) throw new Error(resp.error);
    setStatus('studioStatus', `Imported ${resp.imported} cards from "${resp.deck}"`, 'success');
    await recordImport(resp.deck, resp.imported, resp.uuids || []);
    refreshStats();
    notifyContentScripts();
  } catch (e) {
    setStatus('studioStatus', `Failed: ${e.message}`, 'error');
  } finally {
    $('#importStudioBtn').disabled = false;
  }
});

$('#importAllBtn').addEventListener('click', async () => {
  const baseUrl = $('#studioUrl').value.replace(/\/+$/, '');
  setStatus('studioStatus', 'Loading deck list...', 'info');
  $('#importAllBtn').disabled = true;

  try {
    const listResp = await browser.runtime.sendMessage({
      type: 'list-decks',
      url: `${baseUrl}/api/decks`,
    });
    if (!listResp.success) throw new Error(listResp.error);
    const decks = listResp.decks || [];
    if (decks.length === 0) throw new Error('No decks found');

    let totalImported = 0;
    for (let i = 0; i < decks.length; i++) {
      const deck = decks[i];
      setStatus('studioStatus', `Importing ${deck.name || deck.id} (${i + 1}/${decks.length})...`, 'info');
      const resp = await browser.runtime.sendMessage({
        type: 'fetch-manifest',
        url: `${baseUrl}/api/decks/${deck.id}/export-manifest`,
        source: 'local',
      });
      if (resp.success) {
        totalImported += resp.imported;
        await recordImport(resp.deck || deck.name || deck.id, resp.imported, resp.uuids || []);
      }
    }

    setStatus('studioStatus', `Imported ${totalImported} cards from ${decks.length} deck(s)`, 'success');
    refreshStats();
    notifyContentScripts();
  } catch (e) {
    setStatus('studioStatus', `Failed: ${e.message}`, 'error');
  } finally {
    $('#importAllBtn').disabled = false;
  }
});

// ── Import shared art (opens in a tab) ─────────────────────────────────
// Firefox closes extension popups when a file picker dialog opens,
// destroying the JS context. Opening a dedicated page in a tab avoids this.

$('#openImportBtn').addEventListener('click', () => {
  browser.tabs.create({ url: browser.runtime.getURL('import.html') });
  window.close();
});

// ── Export ──────────────────────────────────────────────────────────────

$('#exportBtn').addEventListener('click', async () => {
  try {
    const resp = await browser.runtime.sendMessage({ type: 'export-all' });
    if (!resp.success) throw new Error(resp.error);

    const manifest = resp.manifest;
    // Count cards across v1 (single deck) or v2 (multi-deck) formats
    let cardCount = 0;
    if (manifest.cards) {
      cardCount = Object.keys(manifest.cards).length;
    } else if (manifest.decks) {
      for (const dk of Object.values(manifest.decks)) {
        cardCount += Object.keys(dk.cards || {}).length;
      }
    }
    if (cardCount === 0) {
      alert('No cards to export.');
      return;
    }

    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'deck-art-manifest.json';
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert('Export failed: ' + e.message);
  }
});

// ── Clear all ──────────────────────────────────────────────────────────

$('#clearBtn').addEventListener('click', async () => {
  if (!confirm('Clear all cached card art? This cannot be undone.')) return;
  try {
    await browser.runtime.sendMessage({ type: 'clear-all' });
    currentActiveDeck = null;
    await browser.storage.local.remove(['importedDecks', 'activeDeck']);
    renderImportedDecks({});
    refreshStats();
    notifyContentScripts();
  } catch (e) {
    alert('Failed: ' + e.message);
  }
});

// ── Helpers ────────────────────────────────────────────────────────────

async function notifyContentScripts() {
  try {
    const tabs = await browser.tabs.query({ url: '*://edhplay.com/*' });
    for (const tab of tabs) {
      browser.tabs.sendMessage(tab.id, { type: 'refresh-db' }).catch(() => {});
    }
  } catch {}
}

// ── Init ───────────────────────────────────────────────────────────────

refreshStats();
loadImportedDecks();
// loadDecks() is called after storage restore (see above) to avoid race condition

// Refresh deck list when storage changes (e.g. import page finishes in another tab)
browser.storage.onChanged.addListener((changes) => {
  if (changes.importedDecks || changes.activeDeck) {
    loadImportedDecks();
    refreshStats();
  }
});
