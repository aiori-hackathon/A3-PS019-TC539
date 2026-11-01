import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const backend = process.env.FIELDLEDGER_BACKEND ?? "http://127.0.0.1:8765";

// Development: Vite serves the UI on 127.0.0.1:5173 and proxies /api to the Python backend, so the
// browser sees one origin. The proxy rewrites Origin to the backend's own origin because the
// backend rejects state-changing requests whose Origin differs from its Host.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/fieldledger/web/static",
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 0, // keep fonts as files: the CSP only allows font-src 'self'
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: backend,
        changeOrigin: true,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        configure: (proxy: any) => {
          proxy.on("proxyReq", (req: { getHeader: (n: string) => unknown; setHeader: (n: string, v: string) => void }) => {
            if (req.getHeader("origin")) req.setHeader("origin", backend);
          });
        },
      },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },
});
