export interface NotificationActionPayload {
  extra?: Record<string, unknown>;
}

export function routeFromNotificationAction(notification: NotificationActionPayload): string | null {
  const route = notification.extra?.route;
  if (typeof route !== "string") return null;
  if (!route.startsWith("/") || route.startsWith("//")) return null;
  return route;
}
