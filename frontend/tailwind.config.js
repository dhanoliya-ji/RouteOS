/** @type {import('tailwindcss').Config} */
export default {
  // Files scanned for class names. Tailwind generates ONLY the utilities it
  // finds here, which is what keeps the stylesheet small.
  //
  // Two consequences worth knowing, both from the same cause: the scan is a
  // regex over each file's TEXT, not a parse of its code.
  //
  // 1. A class assembled at runtime is invisible to it and gets purged. That
  //    is why the Leaflet markers in src/utils/map.ts use inline styles
  //    rather than Tailwind classes: their markup is a string handed to
  //    Leaflet, not source Tailwind can read.
  //
  // 2. Text inside COMMENTS is scanned too, so an ordinary English word that
  //    happens to be a utility name ("hidden", "container", "visible",
  //    "static", "transition", "absolute", "grow", "ring") makes Tailwind
  //    emit that rule even though nothing uses it. The prose comments in
  //    this project add ~950 bytes of such dead CSS: about 5% of the
  //    stylesheet, far less gzipped. Accepted deliberately, since rewording
  //    documentation to dodge common English words would cost more than the
  //    bytes are worth.
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    // `extend`, not a replacement, so Tailwind's own palette (red, green, amber
    // — used by the status badges) stays available alongside these.
    extend: {
      colors: {
        // Two named scales, and the whole UI is built from them. Sticking to two
        // is what makes the app look like one product.
        //
        // brand — the accent: primary buttons, active nav, links.
        brand: {
          50: "#eef4ff", 100: "#d9e6ff", 200: "#bcd2ff", 300: "#8db4ff",
          400: "#578bff", 500: "#2f66f6", 600: "#1a4be0", 700: "#163bb8",
          800: "#183494", 900: "#192f75",
        },
        // ink — everything structural. 900 is body text, 500-400 muted text,
        // 300-200 borders, 50 the page background.
        ink: {
          50: "#f6f7f9", 100: "#eceef2", 200: "#d5dae2", 300: "#b0b9c8",
          400: "#8593a8", 500: "#66748c", 600: "#515d73", 700: "#434c5e",
          800: "#3a4150", 900: "#0f172a",
        },
      },
      fontFamily: {
        // Inter is loaded from Google Fonts in index.html. The fallbacks matter:
        // if that request is blocked the app still renders in the system font
        // rather than a serif default.
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
