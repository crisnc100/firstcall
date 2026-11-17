import { defineConfig } from "vite";

// Two pages: the public front page at "/" and the signed-in app, which the server returns
// for every other page address. Paths are relative to web/, where the build runs.
export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: { front: "index.html", app: "app.html" },
    },
  },
});
