import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // The frozen wire contract lives one level up so both apps import it.
      "@shared": path.resolve(__dirname, "../shared"),
    },
  },
  server: { port: 5173 },
  build: {
    rollupOptions: {
      output: {
        // MapLibre is ~900 kB on its own and changes never; keep it in a chunk
        // of its own so an app deploy does not invalidate it in the browser.
        manualChunks: { maplibre: ["maplibre-gl"] },
      },
    },
  },
});
