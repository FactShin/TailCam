import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Built output goes INTO the Python package so pip-installed AnyCam can serve
// the dashboard with no Node build step on the host.
const OUT_DIR = "../src/anycam/web/spa";

// Dev: proxy the API/stream/media/proxy paths to a locally running AnyCam.
const target = process.env.ANYCAM_DEV_TARGET || "http://localhost:8088";
const proxy = Object.fromEntries(
  ["/api", "/stream", "/media", "/proxy"].map((p) => [p, { target, changeOrigin: true }]),
);

export default defineConfig({
  base: "/",
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        name: "AnyCam",
        short_name: "AnyCam",
        description: "View any webcam from anywhere over Tailscale",
        theme_color: "#0f1115",
        background_color: "#0f1115",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // The app shell is cacheable; live video/API are network-only.
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api/, /^\/stream/, /^\/media/, /^\/proxy/],
        runtimeCaching: [
          {
            urlPattern: ({ url }) =>
              ["/api", "/stream", "/media", "/proxy"].some((p) => url.pathname.startsWith(p)),
            handler: "NetworkOnly",
          },
        ],
      },
    }),
  ],
  build: {
    outDir: OUT_DIR,
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: { proxy },
});
