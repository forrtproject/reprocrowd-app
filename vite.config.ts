import { defineConfig } from "vite";

// The UI (src/renderer) is served to the windows from build/renderer through the app:// protocol.
export default defineConfig({
  root: "src/renderer",
  base: "./",
  build: {
    target: "chrome130",
    outDir: "../../build/renderer",
    emptyOutDir: true,
    modulePreload: false,
  },
});
