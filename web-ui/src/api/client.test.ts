import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  getFleetNodeAudit,
  getFleetNodeCapabilities,
  getFleetNodeHealth,
  reloadFleetNode,
} from "./client";

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("fleet relay client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  test("builds explicit fleet node relay URLs", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await getFleetNodeHealth("garage/pi");
    await getFleetNodeCapabilities("garage/pi");
    await getFleetNodeAudit("garage/pi", 25, 5);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/fleet/nodes/garage%2Fpi/health",
      "/api/v1/fleet/nodes/garage%2Fpi/capabilities",
      "/api/v1/fleet/nodes/garage%2Fpi/audit?limit=25&offset=5",
    ]);
  });

  test("reloads through the fleet relay with POST", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ ok: true, message: "Reload scheduled" }));
    vi.stubGlobal("fetch", fetchMock);

    await reloadFleetNode("local");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/fleet/nodes/local/actions/reload",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
