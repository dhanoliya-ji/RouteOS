// Ambient type declarations. No runtime code.
/// <reference types="vite/client" />

// Types the env vars read in api/client.ts, so a typo in
// import.meta.env.VITE_API_HOTS is a compile error rather than undefined.
// All three are inlined by Vite at BUILD time.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_WS_BASE_URL: string;
  /** Bare backend hostname (no scheme), injected by managed hosts like Render. */
  readonly VITE_API_HOST: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Lets TypeScript accept `import iconUrl from "….png"`. Vite turns such an
// import into the built asset's URL, which is what utils/map.ts relies on to
// repair Leaflet's marker images — without this declaration that import is a
// type error.
declare module "*.png" {
  const src: string;
  export default src;
}
