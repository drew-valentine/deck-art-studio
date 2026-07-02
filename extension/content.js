/**
 * Content script for edhplay.com — replaces Scryfall card images with custom art.
 *
 * Watches the DOM for <img> elements pointing to cards.scryfall.io and swaps
 * them with art cached in the extension's IndexedDB (accessed via background script).
 */
(() => {
  const UUID_RE = /([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/;
  const ATTR = 'data-deck-art';

  // UUID → object URL cache (in-memory for this page session)
  const blobUrlCache = new Map();
  // UUID → original src (for toggle/restore)
  const originalSrcCache = new Map();

  let artEnabled = true;
  let cardMap = null; // Map<uuid, dataUrl> — loaded from background script

  async function loadCardMap() {
    try {
      // Request all cards from background script (which owns the IndexedDB)
      const resp = await browser.runtime.sendMessage({ type: 'get-all-cards' });
      cardMap = new Map();
      if (resp && resp.cards) {
        for (const card of resp.cards) {
          cardMap.set(card.uuid, card.dataUrl);
        }
      }
      console.log(`[Deck Art Studio] Loaded ${cardMap.size} custom card images`);
    } catch (e) {
      console.error('[Deck Art Studio] Failed to load card DB:', e);
      cardMap = new Map();
    }
  }

  function extractUUID(src) {
    const m = src.match(UUID_RE);
    return m ? m[1] : null;
  }

  function getBlobUrl(uuid) {
    if (blobUrlCache.has(uuid)) return blobUrlCache.get(uuid);
    const dataUrl = cardMap.get(uuid);
    if (!dataUrl) return null;

    // Convert data URL to blob URL for efficient rendering
    try {
      const [header, b64] = dataUrl.split(',');
      const mime = header.match(/:(.*?);/)[1];
      const bytes = atob(b64);
      const arr = new Uint8Array(bytes.length);
      for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
      const blob = new Blob([arr], { type: mime });
      const url = URL.createObjectURL(blob);
      blobUrlCache.set(uuid, url);
      return url;
    } catch (e) {
      console.error(`[Deck Art Studio] Failed to create blob URL for ${uuid}:`, e);
      return null;
    }
  }

  // UUIDs we've already tried resolving (avoid repeated API calls)
  const resolvedUUIDs = new Set();

  function processImage(img) {
    if (!artEnabled) return;
    const src = img.src || img.getAttribute('src') || '';
    // Replace card backs with custom card back art if available
    if (src.includes('backs.scryfall.io')) {
      if (img.getAttribute(ATTR) === 'card-back') return; // Already replaced
      const backUrl = cardMap && cardMap.get('card-back');
      if (backUrl) {
        const blobUrl = getBlobUrl('card-back');
        if (blobUrl) {
          originalSrcCache.set(img, src);
          img.setAttribute(ATTR, 'card-back');
          img.src = blobUrl;
        }
      }
      return;
    }
    if (img.getAttribute(ATTR)) return; // Already processed
    if (!src.includes('cards.scryfall.io')) return;

    const uuid = extractUUID(src);
    if (!uuid || !cardMap) return;

    // Double-faced cards share one UUID; the back face's image URL contains
    // "/back/" and its art is stored under "<uuid>:back" in the manifest.
    const isBack = src.includes('/back/');
    const key = isBack ? `${uuid}:back` : uuid;

    if (cardMap.has(key)) {
      // Direct UUID match
      const blobUrl = getBlobUrl(key);
      if (!blobUrl) return;
      originalSrcCache.set(img, src);
      img.setAttribute(ATTR, key);
      img.src = blobUrl;
    } else if (!resolvedUUIDs.has(key)) {
      // UUID not in our map — ask background to resolve via Scryfall API
      resolvedUUIDs.add(key);
      browser.runtime.sendMessage({ type: 'resolve-uuid', uuid, face: isBack ? 'back' : 'front' }).then(resp => {
        if (resp && resp.dataUrl) {
          // Add to our local map so future hits are instant
          cardMap.set(key, resp.dataUrl);
          console.log(`[Deck Art Studio] Resolved ${resp.name} (alternate printing)`);
          // Replace this image and re-scan for others with same UUID
          scanAll();
        }
      }).catch(() => {});
    }
  }

  function scanAll() {
    const images = document.querySelectorAll('img[src*="cards.scryfall.io"]');
    images.forEach(processImage);
    // Also process card backs
    const backs = document.querySelectorAll('img[src*="backs.scryfall.io"]');
    backs.forEach(processImage);
    // Also check images we've already replaced (React may change src back)
    const replaced = document.querySelectorAll(`img[${ATTR}]`);
    replaced.forEach(img => {
      if (!artEnabled) return;
      const src = img.src || '';
      // If React reset the src back to Scryfall, re-replace
      if (src.includes('cards.scryfall.io')) {
        const uuid = img.getAttribute(ATTR);
        const blobUrl = getBlobUrl(uuid);
        if (blobUrl) img.src = blobUrl;
      }
    });
  }

  function restoreAll() {
    const replaced = document.querySelectorAll(`img[${ATTR}]`);
    replaced.forEach(img => {
      const orig = originalSrcCache.get(img);
      if (orig) {
        img.src = orig;
      }
      img.removeAttribute(ATTR);
    });
  }

  // MutationObserver — watches for new/changed images
  const observer = new MutationObserver((mutations) => {
    if (!cardMap || cardMap.size === 0) return;
    for (const mutation of mutations) {
      // New nodes added
      if (mutation.type === 'childList') {
        mutation.addedNodes.forEach(node => {
          if (node.nodeType !== 1) return;
          if (node.tagName === 'IMG') processImage(node);
          // Check children
          const imgs = node.querySelectorAll?.('img');
          if (imgs) imgs.forEach(processImage);
        });
      }
      // Attribute change on an img (React may swap src)
      if (mutation.type === 'attributes' && mutation.target.tagName === 'IMG') {
        const img = mutation.target;
        // Only re-process if src changed back to Scryfall
        if ((img.src || '').includes('cards.scryfall.io')) {
          img.removeAttribute(ATTR);
          processImage(img);
        }
      }
    }
  });

  // Listen for messages from popup/background
  browser.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'toggle-art') {
      artEnabled = msg.enabled;
      if (artEnabled) {
        scanAll();
      } else {
        restoreAll();
      }
    }
    if (msg.type === 'refresh-db') {
      // Clear all caches and restore originals before reloading
      restoreAll();
      blobUrlCache.forEach(url => URL.revokeObjectURL(url));
      blobUrlCache.clear();
      resolvedUUIDs.clear();
      loadCardMap().then(scanAll);
    }
  });

  // Initialize
  async function init() {
    await loadCardMap();
    if (cardMap.size === 0) {
      console.log('[Deck Art Studio] No custom art loaded — extension idle');
      // Still observe in case art is imported later
    }
    scanAll();
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['src'],
    });
    console.log('[Deck Art Studio] MutationObserver active on edhplay.com');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
