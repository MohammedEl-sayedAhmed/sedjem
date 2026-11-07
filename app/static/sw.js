// Minimal service worker so the browser offers "Install app" (opens Sedjem in its own window).
// It caches nothing: every request goes to the local server as usual.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
