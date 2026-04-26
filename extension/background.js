/**
 * Background service worker for Deck Art Studio extension.
 * Handles manifest fetching and IndexedDB operations.
 */

// db.js is loaded via manifest background.scripts

/**
 * Convert a Google Drive share link to a direct download URL.
 */
function toDirectUrl(url) {
  let m = url.match(/drive\.google\.com\/file\/d\/([^/]+)/);
  if (m) return `https://drive.google.com/uc?export=download&id=${m[1]}`;
  m = url.match(/drive\.google\.com\/open\?id=([^&]+)/);
  if (m) return `https://drive.google.com/uc?export=download&id=${m[1]}`;
  return url;
}

/**
 * Import a single-deck manifest into IndexedDB.
 * Each card is scoped to the deck name so different decks keep separate art.
 */
async function importSingleDeck(deckName, cards, source = 'local') {
  let imported = 0, skipped = 0, errors = 0;
  const uuids = [];

  for (const [uuid, card] of Object.entries(cards)) {
    if (!card.image) { skipped++; continue; }
    try {
      await DeckArtDB.putCard(deckName, uuid, card.name || '', card.image, source);
      uuids.push(uuid);
      imported++;
    } catch (e) {
      console.error(`[Deck Art Studio] Failed to store ${card.name}:`, e);
      errors++;
    }
  }

  return { imported, skipped, errors, deck: deckName, uuids };
}

/**
 * Import a manifest JSON object into IndexedDB.
 * Supports v1 (single deck) and v2 (multi-deck) formats.
 * Returns results for each deck imported.
 */
async function importManifest(manifest, source = 'local') {
  if (!manifest) throw new Error('Invalid manifest');

  // v2 multi-deck format
  if (manifest.decks && typeof manifest.decks === 'object') {
    const results = [];
    for (const [name, data] of Object.entries(manifest.decks)) {
      if (!data.cards || typeof data.cards !== 'object') continue;
      const result = await importSingleDeck(name, data.cards, source);
      results.push(result);
    }
    if (results.length === 0) throw new Error('Manifest has no decks with cards');
    // Aggregate results
    const total = results.reduce((acc, r) => ({
      imported: acc.imported + r.imported,
      skipped: acc.skipped + r.skipped,
      errors: acc.errors + r.errors,
    }), { imported: 0, skipped: 0, errors: 0 });
    return {
      ...total,
      deck: results.map(r => r.deck).join(', '),
      uuids: results.flatMap(r => r.uuids),
      deckResults: results,
    };
  }

  // v1 single-deck format
  if (manifest.cards && typeof manifest.cards === 'object') {
    return importSingleDeck(manifest.deck || 'Unknown', manifest.cards, source);
  }

  throw new Error('Invalid manifest: missing "cards" or "decks"');
}

/**
 * Fetch a manifest from a URL and import it.
 */
async function fetchAndImport(url, source = 'shared') {
  const directUrl = toDirectUrl(url);
  const resp = await fetch(directUrl);
  if (!resp.ok) throw new Error(`Fetch failed: ${resp.status} ${resp.statusText}`);
  const manifest = await resp.json();
  return importManifest(manifest, source);
}

/**
 * Get the active deck name and its cards from IndexedDB.
 * Returns all cards if no active deck is set.
 */
async function getActiveDeckCards() {
  const data = await chrome.storage.local.get('activeDeck');
  const activeDeck = data.activeDeck;
  if (activeDeck) {
    return DeckArtDB.getCardsByDeck(activeDeck);
  }
  return DeckArtDB.getAllCards();
}

// Handle messages from popup and content scripts
if (typeof chrome !== 'undefined' && chrome.runtime) {
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === 'import-manifest') {
      importManifest(msg.manifest, msg.source || 'local')
        .then(result => sendResponse({ success: true, ...result }))
        .catch(e => sendResponse({ success: false, error: e.message }));
      return true;
    }

    if (msg.type === 'fetch-manifest') {
      fetchAndImport(msg.url, msg.source || 'shared')
        .then(result => sendResponse({ success: true, ...result }))
        .catch(e => sendResponse({ success: false, error: e.message }));
      return true;
    }

    if (msg.type === 'list-decks') {
      fetch(msg.url)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => sendResponse({ success: true, ...data }))
        .catch(e => sendResponse({ success: false, error: e.message }));
      return true;
    }

    if (msg.type === 'get-all-cards') {
      getActiveDeckCards()
        .then(cards => sendResponse({ cards }))
        .catch(() => sendResponse({ cards: [] }));
      return true;
    }

    // Resolve an unknown UUID → card name via Scryfall API, then match by name
    // within the active deck only
    if (msg.type === 'resolve-uuid') {
      (async () => {
        try {
          const resp = await fetch(`https://api.scryfall.com/cards/${msg.uuid}`);
          if (!resp.ok) { sendResponse({ dataUrl: null }); return; }
          const card = await resp.json();
          const name = card.name || '';
          if (!name) { sendResponse({ dataUrl: null }); return; }

          // Search within active deck's cards by name
          const deckCards = await getActiveDeckCards();
          const match = deckCards.find(c => c.name === name);
          if (match) {
            // Cache this UUID under the same deck for future hits
            await DeckArtDB.putCard(match.deck, msg.uuid, name, match.dataUrl, 'resolved');
            sendResponse({ dataUrl: match.dataUrl, name });
          } else {
            sendResponse({ dataUrl: null });
          }
        } catch (e) {
          sendResponse({ dataUrl: null });
        }
      })();
      return true;
    }

    if (msg.type === 'get-stats') {
      DeckArtDB.count()
        .then(count => sendResponse({ count }))
        .catch(() => sendResponse({ count: 0 }));
      return true;
    }

    if (msg.type === 'clear-all') {
      DeckArtDB.clearAll()
        .then(() => sendResponse({ success: true }))
        .catch(e => sendResponse({ success: false, error: e.message }));
      return true;
    }

    if (msg.type === 'delete-deck') {
      DeckArtDB.deleteByDeck(msg.deck)
        .then(() => sendResponse({ success: true }))
        .catch(e => sendResponse({ success: false, error: e.message }));
      return true;
    }

    if (msg.type === 'export-all') {
      getActiveDeckCards()
        .then(cards => {
          // Group cards by deck name to preserve deck structure
          const byDeck = {};
          for (const card of cards) {
            const dk = card.deck || 'Unknown';
            if (!byDeck[dk]) byDeck[dk] = {};
            byDeck[dk][card.uuid] = {
              name: card.name,
              image: card.dataUrl,
            };
          }

          const deckNames = Object.keys(byDeck);
          let manifest;
          if (deckNames.length === 1) {
            // Single deck — use v1 format for max compatibility
            manifest = {
              version: 1,
              deck: deckNames[0],
              cards: byDeck[deckNames[0]],
            };
          } else {
            // Multiple decks — v2 multi-deck format
            manifest = {
              version: 2,
              decks: {},
            };
            for (const [name, cards] of Object.entries(byDeck)) {
              manifest.decks[name] = { cards };
            }
          }

          sendResponse({ success: true, manifest });
        })
        .catch(e => sendResponse({ success: false, error: e.message }));
      return true;
    }
  });
}

// Notify content scripts to refresh when art is imported
function notifyContentScripts() {
  if (typeof chrome !== 'undefined' && chrome.tabs) {
    chrome.tabs.query({ url: '*://edhplay.com/*' }, (tabs) => {
      for (const tab of tabs) {
        chrome.tabs.sendMessage(tab.id, { type: 'refresh-db' }).catch(() => {});
      }
    });
  }
}
