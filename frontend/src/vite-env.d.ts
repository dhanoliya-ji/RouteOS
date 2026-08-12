/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_WS_BASE_URL: string;
  /** Bare backend hostname (no scheme), injected by managed hosts like Render. */
  readonly VITE_API_HOST: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module "*.png" {
  const src: string;
  export default src;
}
