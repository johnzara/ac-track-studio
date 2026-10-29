/* Street View for the survey, opened in Google Maps itself (a separate window) through keyless
   Maps URLs. Google's Maps Platform terms do not allow Street View imagery on the same screen as a
   non-Google map, so the planner does not embed a panorama next to its OpenStreetMap view; it
   steers one Google Maps window along the route instead. No key, no Google data in the planner,
   nothing saved but the user's own notes. */
(function () {
  "use strict";
  const NAME = "acts_streetview";
  let win = null;

  function url(lon, lat, heading) {
    const h = Math.round(((heading || 0) % 360 + 360) % 360);
    return `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat.toFixed(6)},${lon.toFixed(6)}&heading=${h}&pitch=0&fov=90`;
  }

  /** Open (or move) the Street View window. Call it from a click or key press (pop-up blockers). */
  function open(lon, lat, heading) {
    const u = url(lon, lat, heading);
    win = window.open(u, NAME);
    if (!win) return false;
    return true;
  }

  function isOpen() { return !!win && !win.closed; }

  window.StreetView = { open, url, isOpen };
})();
