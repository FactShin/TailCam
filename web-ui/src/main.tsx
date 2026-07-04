import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./app/AppShell";
import { ToastProvider } from "./components/toast";
import { AiStudio } from "./screens/AiStudio";
import { CameraDetail } from "./screens/CameraDetail";
import { Dashboard } from "./screens/Dashboard";
import { Docs } from "./screens/Docs";
import { Events } from "./screens/Events";
import { Gallery } from "./screens/Gallery";
import { McpSetup } from "./screens/McpSetup";
import { Plugins } from "./screens/Plugins";
import { Settings } from "./screens/Settings";
import { Timelapse } from "./screens/Timelapse";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 1000 },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <AppShell>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/camera/:host/:cid" element={<CameraDetail />} />
              <Route path="/gallery" element={<Gallery />} />
              <Route path="/events" element={<Events />} />
              <Route path="/ai" element={<AiStudio />} />
              <Route path="/timelapse" element={<Timelapse />} />
              {/* Old destinations now live as AI Studio tabs. */}
              <Route path="/training" element={<Navigate to="/ai?tab=training" replace />} />
              <Route path="/models" element={<Navigate to="/ai?tab=models" replace />} />
              <Route path="/agents" element={<McpSetup />} />
              <Route path="/plugins" element={<Plugins />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/docs" element={<Docs />} />
              <Route path="/docs/:slug" element={<Docs />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppShell>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
