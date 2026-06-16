import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { desktopRuntime } from "./runtime";
import { routeFromNotificationAction } from "./notificationRouting";

export function DesktopNotificationRouter() {
  const navigate = useNavigate();

  useEffect(() => {
    if (!desktopRuntime.isDesktop) return;

    let disposed = false;
    let unregister: (() => Promise<void>) | null = null;

    void import("@tauri-apps/plugin-notification")
      .then(({ onAction }) =>
        onAction((notification) => {
          const route = routeFromNotificationAction(notification);
          if (!route) return;
          void desktopRuntime.openMainRoute(route).catch(() => {
            void desktopRuntime.openMainWindow();
            navigate(route);
          });
        }),
      )
      .then((listener) => {
        if (disposed) {
          void listener.unregister();
        } else {
          unregister = listener.unregister.bind(listener);
        }
      })
      .catch(() => undefined);

    return () => {
      disposed = true;
      if (unregister) void unregister();
    };
  }, [navigate]);

  return null;
}
