/**
 * Chrome MV3 service worker entry point.
 * Imports db.js and background.js so Chrome can use a single service_worker file.
 * Firefox uses background.scripts directly and ignores this file.
 */
importScripts('db.js', 'background.js');
