/**
 * IndexedDB wrapper for Deck Art Studio extension.
 * Cards are scoped by deck — each deck has its own copy of each card,
 * so shared cards (Sol Ring, basics) keep per-deck art.
 *
 * Cards store key: "deckName|uuid"
 * Indexes: deck, uuid, name
 */
const DeckArtDB = (() => {
  const DB_NAME = 'DeckArtDB';
  const DB_VERSION = 3;
  const CARDS_STORE = 'cards';

  let _dbCache = null;

  function open() {
    if (_dbCache) return Promise.resolve(_dbCache);
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        const tx = e.target.transaction;
        // Only recreate the store if schema needs updating
        if (!db.objectStoreNames.contains(CARDS_STORE)) {
          const store = db.createObjectStore(CARDS_STORE, { keyPath: 'id' });
          store.createIndex('deck', 'deck', { unique: false });
          store.createIndex('uuid', 'uuid', { unique: false });
          store.createIndex('name', 'name', { unique: false });
        } else {
          // Ensure indexes exist on existing store
          const store = tx.objectStore(CARDS_STORE);
          if (!store.indexNames.contains('deck'))
            store.createIndex('deck', 'deck', { unique: false });
          if (!store.indexNames.contains('uuid'))
            store.createIndex('uuid', 'uuid', { unique: false });
          if (!store.indexNames.contains('name'))
            store.createIndex('name', 'name', { unique: false });
        }
        // Clean up legacy stores
        if (db.objectStoreNames.contains('names')) {
          db.deleteObjectStore('names');
        }
      };
      req.onsuccess = () => {
        _dbCache = req.result;
        // Clear cache if the connection closes unexpectedly
        _dbCache.onclose = () => { _dbCache = null; };
        _dbCache.onversionchange = () => {
          _dbCache.close();
          _dbCache = null;
        };
        resolve(_dbCache);
      };
      req.onerror = () => reject(req.error);
    });
  }

  function makeId(deck, uuid) {
    return deck + '|' + uuid;
  }

  async function putCard(deck, uuid, name, dataUrl, source = 'local') {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(CARDS_STORE, 'readwrite');
      tx.objectStore(CARDS_STORE).put({
        id: makeId(deck, uuid),
        deck, uuid, name, dataUrl, source,
      });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async function getCardsByDeck(deck) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(CARDS_STORE, 'readonly');
      const idx = tx.objectStore(CARDS_STORE).index('deck');
      const req = idx.getAll(deck);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function getAllCards() {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(CARDS_STORE, 'readonly');
      const req = tx.objectStore(CARDS_STORE).getAll();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function deleteByDeck(deck) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(CARDS_STORE, 'readwrite');
      const store = tx.objectStore(CARDS_STORE);
      const idx = store.index('deck');
      const req = idx.openCursor(deck);
      req.onsuccess = () => {
        const cursor = req.result;
        if (cursor) {
          cursor.delete();
          cursor.continue();
        }
      };
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async function clearAll() {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(CARDS_STORE, 'readwrite');
      tx.objectStore(CARDS_STORE).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async function count() {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(CARDS_STORE, 'readonly');
      const req = tx.objectStore(CARDS_STORE).count();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  return { open, putCard, getCardsByDeck, getAllCards, deleteByDeck, clearAll, count };
})();

if (typeof module !== 'undefined') module.exports = DeckArtDB;
