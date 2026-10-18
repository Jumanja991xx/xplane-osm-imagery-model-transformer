import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Port 1420 is what the Tauri shell loads in development (devUrl).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: { target: "chrome110", sourcemap: false, chunkSizeWarningLimit: 2000 },
  worker: { format: "es" },
});
