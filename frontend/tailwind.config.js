/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff", 100: "#d9e6ff", 200: "#bcd2ff", 300: "#8db4ff",
          400: "#578bff", 500: "#2f66f6", 600: "#1a4be0", 700: "#163bb8",
          800: "#183494", 900: "#192f75",
        },
        ink: {
          50: "#f6f7f9", 100: "#eceef2", 200: "#d5dae2", 300: "#b0b9c8",
          400: "#8593a8", 500: "#66748c", 600: "#515d73", 700: "#434c5e",
          800: "#3a4150", 900: "#0f172a",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
