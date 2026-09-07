# `src/utils/` — map helpers

One file, `map.ts`: everything Leaflet needs that is not a React component.
Imported by the three screens that render a map — `RoutePlanner`, `LiveOps` and
`Depots`.

| Export | Is |
|---|---|
| `NCR_CENTER` | The default map centre, `[28.55, 77.25]` |
| `ROUTE_COLORS` | Ten colours, cycled so each route polyline is distinguishable |
| `coloredDot(color, size)` | A round marker for a delivery stop |
| `vehicleIcon(color)` | A square marker for a moving vehicle |
| `depotIcon` | The depot marker |

---

## The import side effect at the top

The first thing in the file, and the least obvious:

```js
import iconUrl from "leaflet/dist/images/marker-icon.png";
import iconRetinaUrl from "leaflet/dist/images/marker-icon-2x.png";
import shadowUrl from "leaflet/dist/images/marker-shadow.png";

L.Icon.Default.mergeOptions({ iconUrl, iconRetinaUrl, shadowUrl });
```

**Why it is needed.** Leaflet ships CSS that points at its marker images by
*relative path*. Under a bundler those files are hashed and moved, so the paths
Leaflet computes no longer resolve and every default marker renders as a broken
image. Importing the three PNGs lets Vite rewrite them to real built URLs, and
`mergeOptions` hands those back to Leaflet.

This is a module-level side effect: importing `utils/map` is what fixes the
icons, so the import must not be removed as "unused" even in a file that only
wants `NCR_CENTER`. (The `declare module "*.png"` in `src/vite-env.d.ts` is what
makes TypeScript accept importing an image at all.)

---

## Why the icons are `divIcon`s

`coloredDot` and `vehicleIcon` return `L.divIcon` — a marker made of HTML —
rather than image files:

```js
html: `<div style="width:${size}px;height:${size}px;background:${color};…"></div>`
```

The reason is that the colour must be **dynamic**. A route's colour is decided
at render time by its index, so a marker per colour cannot be a static asset —
it would mean ten pre-made PNGs per shape, and nothing new when an eleventh
route appears. Inline HTML takes the colour as a parameter.

`className: ""` is deliberate: Leaflet's default marker class adds styling that
would fight the inline styles here.

The two shapes carry meaning — **round is a stop, square is a vehicle** — so the
two are distinguishable on a busy map even when they share a route colour.

Note the styles are inline rather than Tailwind classes. They have to be:
this HTML is handed to Leaflet as a string and inserted outside React's tree,
so Tailwind's build-time class scanning would not see the classes and would
purge them from the CSS.

---

## `ROUTE_COLORS` and cycling

Ten hand-picked, visually distinct colours. Every consumer indexes with a
modulo:

```js
ROUTE_COLORS[i % ROUTE_COLORS.length]
```

So an eleventh route reuses the first colour rather than reading `undefined`.
Colours are assigned by **position in the list**, not by route id — which means
a route's colour is stable within one render but can change between renders if
the list reorders. Acceptable for a map legend; it would not do for anything
persistent.

---

## `NCR_CENTER`

The initial view for every map, matching the demo data's geography (the backend
seeds orders around Delhi NCR — see `backend/scripts/demo_geo.py`). It is only
an initial centre: the user pans freely, and nothing recentres afterwards.

Note the maps do **not** auto-fit to their content. A depot outside the NCR
region would render off-screen until the user panned to it;
`map.fitBounds(...)` over the plotted points would be the fix.
