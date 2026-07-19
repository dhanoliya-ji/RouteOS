import L from "leaflet";

// Fix Leaflet's default icon URLs (they break under bundlers).
import iconUrl from "leaflet/dist/images/marker-icon.png";
import iconRetinaUrl from "leaflet/dist/images/marker-icon-2x.png";
import shadowUrl from "leaflet/dist/images/marker-shadow.png";

L.Icon.Default.mergeOptions({ iconUrl, iconRetinaUrl, shadowUrl });

export const NCR_CENTER: [number, number] = [28.55, 77.25];

// Distinct colors so each route polyline is visually separable.
export const ROUTE_COLORS = [
  "#2f66f6", "#e0561a", "#16a34a", "#9333ea", "#0891b2",
  "#ca8a04", "#db2777", "#4f46e5", "#65a30d", "#dc2626",
];

export function coloredDot(color: string, size = 14): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<div style="width:${size}px;height:${size}px;background:${color};border:2px solid white;border-radius:50%;box-shadow:0 0 0 1px rgba(0,0,0,.25)"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

export function vehicleIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<div style="width:22px;height:22px;background:${color};border:3px solid white;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;color:white;font-size:12px">▲</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

export const depotIcon = L.divIcon({
  className: "",
  html: `<div style="width:26px;height:26px;background:#0f172a;border:3px solid #facc15;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#facc15;font-size:13px">◆</div>`,
  iconSize: [26, 26],
  iconAnchor: [13, 13],
});
