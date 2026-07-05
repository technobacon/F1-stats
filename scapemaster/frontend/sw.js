/* ScapeMaster service worker.
 *
 * Deliberately minimal: it exists so the app can *show* local notifications
 * (the opt-in streak reminder) and, where the browser supports Notification
 * Triggers, have them scheduled for later. There is no push server and no
 * offline caching here — caching the app shell on an ephemeral free host risks
 * serving stale JS/CSS after a deploy, which isn't worth the trade for a trivia
 * game. Keep this file tiny.
 */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

/* Tapping a streak notification focuses an open tab, or opens the app. */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow("/?play=daily");
    })
  );
});
