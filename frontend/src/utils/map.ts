// Leaflet helpers: everything the maps need that is not a React component.
//
// Imported by the three screens that render a map — RoutePlanner, LiveOps and
// Depots.
import L from "leaflet";

// Fix Leaflet's default icon URLs (they break under bundlers).
//
// WHY: Leaflet's stylesheet points at its marker images by relative path. A
// bundler hashes and moves those files, so the paths Leaflet computes no longer
// resolve and every default marker renders as a broken image. Importing the
// PNGs lets Vite rewrite them to real built URLs, which mergeOptions then hands
// back to Leaflet.
//
// This is a MODULE-LEVEL SIDE EFFECT: importing this file is what repairs the
// icons. So the import must not be pruned as "unused" even in a module that
// only wants NCR_CENTER.
//
// (The `declare module "*.png"` in src/vite-env.d.ts is what lets TypeScript
// accept importing an image at all.)
import iconUrl from "leaflet/dist/images/marker-icon.png";
import iconRetinaUrl from "leaflet/dist/images/marker-icon-2x.png";
import shadowUrl from "leaflet/dist/images/marker-shadow.png";

L.Icon.Default.mergeOptions({ iconUrl, iconRetinaUrl, shadowUrl });

// The initial centre for every map, matching the demo data's geography — the
// backend seeds orders around Delhi NCR (see backend/scripts/demo_geo.py).
//
// Only an INITIAL centre: the user pans freely and nothing recentres
// afterwards. Note the maps never fit their bounds either, so a depot outside
// this region renders off-screen until the user goes looking for it;
// map.fitBounds() over the plotted points would be the fix.
export const NCR_CENTER: [number, number] = [28.55, 77.25];

// Distinct colors so each route polyline is visually separable.
//
// Every consumer indexes with `i % ROUTE_COLORS.length`, so an eleventh route
// reuses the first colour rather than reading undefined. Colours are assigned
// by POSITION in the rendered list, not by route id — so a route's colour is
// stable within a render but can change if the list reorders. Fine for a map
// legend; it would not do for anything persisted.
export const ROUTE_COLORS = [
  "#2f66f6", "#e0561a", "#16a34a", "#9333ea", "#0891b2",
  "#ca8a04", "#db2777", "#4f46e5", "#65a30d", "#dc2626",
];

/**
 * A round marker for a delivery stop.
 *
 * A divIcon (marker built from HTML) rather than an image, because the colour
 * has to be dynamic — a route's colour is chosen at render time, so a static
 * asset would mean one pre-made PNG per colour and nothing for an eleventh
 * route.
 *
 * Styles are inline rather than Tailwind classes, and have to be: this markup
 * is handed to Leaflet as a string and inserted outside React's tree, where
 * Tailwind's build-time class scanning cannot see it and would purge the
 * classes from the CSS.
 *
 * ROUND = A STOP, square = a vehicle. The two shapes stay distinguishable even
 * when they share a route colour.
 */
export function coloredDot(color: string, size = 14): L.DivIcon {
  return L.divIcon({
    // Empty on purpose: Leaflet's default marker class would add styling that
    // fights the inline styles below.
    className: "",
    // The white border and faint outer ring are what keep a dot visible
    // against both pale streets and dark parkland on the OSM tiles.
    html: `<div style="width:${size}px;height:${size}px;background:${color};border:2px solid white;border-radius:50%;box-shadow:0 0 0 1px rgba(0,0,0,.25)"></div>`,
    iconSize: [size, size],
    // Half the size, so the marker is CENTRED on its coordinate rather than
    // hanging below-right of it.
    iconAnchor: [size / 2, size / 2],
  });
}

/**
 * A square marker for a moving vehicle, coloured to match its route.
 *
 * Deliberately larger and squarer than a stop dot, with a triangle glyph, so a
 * vehicle reads as different in kind from the stops it is driving between.
 */
export function vehicleIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<div style="width:22px;height:22px;background:${color};border:3px solid white;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;color:white;font-size:12px">▲</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

/**
 * The depot marker.
 *
 * A constant, not a factory, because there is only ever one appearance — a
 * depot has no per-route colour. Dark with a yellow border so the hub stands
 * out from every route colour rather than competing with them.
 */
export const depotIcon = L.divIcon({
  className: "",
  html: `<div style="width:26px;height:26px;background:#0f172a;border:3px solid #facc15;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#facc15;font-size:13px">◆</div>`,
  iconSize: [26, 26],
  iconAnchor: [13, 13],
});
