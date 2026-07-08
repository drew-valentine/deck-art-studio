/**
 * Import page for Deck Art Studio extension.
 * Opens as a full tab so the file picker doesn't close the page
 * (Firefox closes extension popups when a file dialog opens).
 */

const $ = (sel) => document.querySelector(sel);

function setStatus(msg, cls = 'info') {
  const el = $('#importStatus');
  el.textContent = msg;
  el.className = `status ${cls}`;
}

function showProgress(pct) {
  const bar = $('#progressBar');
  const fill = $('#progressFill');
  bar.style.display = 'block';
  fill.style.width = `${pct}%`;
}

function hideProgress() {
  $('#progressBar').style.display = 'none';
}

function showResult(msg, success) {
  const box = $('#resultBox');
  box.textContent = msg;
  box.className = `result ${success ? 'success' : 'error'}`;
  box.style.display = 'block';
}

// ── Record import in storage (mirrors popup.js logic) ──────────────────

async function recordImport(deckName, cardCount, uuids = []) {
  const { importedDecks = {} } = await browser.storage.local.get('importedDecks');
  const existing = importedDecks[deckName]?.uuids || [];
  const merged = [...new Set([...existing, ...uuids])];
  importedDecks[deckName] = {
    cards: cardCount,
    uuids: merged,
    importedAt: Date.now(),
  };
  await browser.storage.local.set({ importedDecks });
  // Auto-activate the just-imported deck
  await browser.storage.local.set({ activeDeck: deckName });
}

async function notifyContentScripts() {
  try {
    const tabs = await browser.tabs.query({ url: '*://edhplay.com/*' });
    for (const tab of tabs) {
      browser.tabs.sendMessage(tab.id, { type: 'refresh-db' }).catch(() => {});
    }
  } catch {}
}

// ── File import ────────────────────────────────────────────────────────

const dropZone = $('#dropZone');
const fileInput = $('#fileInput');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('dragover');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) {
    handleFile(fileInput.files[0]);
    fileInput.value = '';
  }
});

/**
 * Normalize manifest to a list of { deckName, cards: [[uuid, cardObj], ...] } entries.
 * Supports v1 (single deck) and v2 (multi-deck) formats.
 */
function parseManifest(manifest) {
  if (!manifest) throw new Error('Empty manifest');

  // v2 multi-deck format: { version: 2, decks: { "Name": { cards: {...} } } }
  if (manifest.decks && typeof manifest.decks === 'object') {
    const decks = [];
    for (const [name, data] of Object.entries(manifest.decks)) {
      if (!data.cards || typeof data.cards !== 'object') continue;
      const entries = Object.entries(data.cards);
      if (entries.length > 0) decks.push({ deckName: name, cards: entries });
    }
    if (decks.length === 0) throw new Error('Manifest has no decks with cards');
    return decks;
  }

  // v1 single-deck format: { deck: "Name", cards: {...} }
  if (manifest.cards && typeof manifest.cards === 'object') {
    const entries = Object.entries(manifest.cards);
    if (entries.length === 0) throw new Error('Manifest has empty cards object');
    return [{ deckName: manifest.deck || 'Unknown', cards: entries }];
  }

  throw new Error('Invalid manifest: missing "cards" or "decks". Keys found: ' + Object.keys(manifest).join(', '));
}

async function handleFile(file) {
  setStatus(`Reading ${file.name}...`, 'info');
  showProgress(0);

  try {
    const text = await file.text();
    if (!text.trim()) throw new Error('File is empty');

    let manifest;
    try {
      manifest = JSON.parse(text);
    } catch (parseErr) {
      throw new Error('Not valid JSON: ' + parseErr.message);
    }

    const decks = parseManifest(manifest);
    const totalCards = decks.reduce((sum, d) => sum + d.cards.length, 0);
    const deckLabel = decks.length === 1 ? `"${decks[0].deckName}"` : `${decks.length} decks`;
    setStatus(`Importing ${deckLabel} (${totalCards} cards)...`, 'info');

    let totalImported = 0, totalErrors = 0, processed = 0;

    for (const { deckName, cards } of decks) {
      const uuids = [];
      let deckImported = 0;

      for (let i = 0; i < cards.length; i++) {
        const [uuid, card] = cards[i];
        if (!card.image) { processed++; continue; }
        try {
          await DeckArtDB.putCard(deckName, uuid, card.name || '', card.image, 'shared');
          uuids.push(uuid);
          deckImported++;
        } catch (cardErr) {
          console.error(`[Deck Art] Card import failed for ${card.name}:`, cardErr);
          totalErrors++;
        }
        processed++;
        const pct = Math.round((processed / totalCards) * 100);
        showProgress(pct);
        if (processed % 10 === 0 || processed === totalCards) {
          setStatus(`Importing ${deckLabel}... ${processed}/${totalCards}`, 'info');
        }
      }

      if (deckImported > 0) {
        await recordImport(deckName, deckImported, uuids);
        totalImported += deckImported;
      }
    }

    hideProgress();

    if (totalImported === 0) throw new Error(`0 cards imported, ${totalErrors} errors`);

    const msg = `Imported ${totalImported} cards from ${deckLabel}` + (totalErrors ? ` (${totalErrors} errors)` : '');
    setStatus(msg, 'success');
    showResult(msg, true);
    notifyContentScripts();
  } catch (e) {
    hideProgress();
    console.error('[Deck Art] File import failed:', e);
    setStatus(`Failed: ${e.message}`, 'error');
    showResult(`Import failed: ${e.message}`, false);
  }
}

// ── URL import ─────────────────────────────────────────────────────────

$('#fetchUrlBtn').addEventListener('click', async () => {
  const url = $('#manifestUrl').value.trim();
  if (!url) return;

  setStatus('Fetching manifest...', 'info');
  $('#fetchUrlBtn').disabled = true;
  showProgress(0);

  try {
    const resp = await browser.runtime.sendMessage({
      type: 'fetch-manifest',
      url,
      source: 'shared',
    });
    if (!resp.success) throw new Error(resp.error);

    hideProgress();

    // Record each deck separately if multi-deck results are available
    if (resp.deckResults) {
      for (const dr of resp.deckResults) {
        if (dr.imported > 0) await recordImport(dr.deck, dr.imported, dr.uuids || []);
      }
    } else if (resp.imported > 0) {
      await recordImport(resp.deck, resp.imported, resp.uuids || []);
    }

    const msg = `Imported ${resp.imported} cards from "${resp.deck}"`;
    setStatus(msg, 'success');
    showResult(msg, true);
    notifyContentScripts();
  } catch (e) {
    hideProgress();
    setStatus(`Failed: ${e.message}`, 'error');
    showResult(`Fetch failed: ${e.message}`, false);
  } finally {
    $('#fetchUrlBtn').disabled = false;
  }
});
