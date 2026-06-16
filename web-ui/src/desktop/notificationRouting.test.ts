import { describe, expect, test } from "vitest";

import { routeFromNotificationAction } from "./notificationRouting";

describe("desktop notification routing", () => {
  test("accepts same-origin absolute app routes", () => {
    expect(routeFromNotificationAction({ extra: { route: "/events" } })).toBe("/events");
    expect(routeFromNotificationAction({ extra: { route: "/events?camera=front" } })).toBe(
      "/events?camera=front",
    );
  });

  test("rejects missing, relative, and external routes", () => {
    expect(routeFromNotificationAction({})).toBeNull();
    expect(routeFromNotificationAction({ extra: { route: "events" } })).toBeNull();
    expect(routeFromNotificationAction({ extra: { route: "//evil.example" } })).toBeNull();
    expect(routeFromNotificationAction({ extra: { route: "https://evil.example/events" } })).toBeNull();
  });
});
