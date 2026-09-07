// PostCSS pipeline. Vite picks this up automatically for every stylesheet.
//
// Order matters: Tailwind first, to expand its directives and @apply rules into
// real CSS, then autoprefixer over the result.
export default {
  plugins: {
    // Generates the utility classes actually used, based on the `content` globs
    // in tailwind.config.js.
    tailwindcss: {},
    // Adds vendor prefixes for the browsers in the project's browserslist range.
    autoprefixer: {},
  },
};
