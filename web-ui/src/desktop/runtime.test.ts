import { describe, expect, test, vi } from "vitest";

import { createDesktopRuntime, type TauriInvoke } from "./runtime";

describe("desktop runtime", () => {
  test("browser fallback is safe and never loads Tauri", async () => {
    const loadInvoke = vi.fn();
    const runtime = createDesktopRuntime({ isDesktop: false, loadInvoke });

    await runtime.openMainWindow();
    await runtime.quit();
    await runtime.setLaunchAtLogin(true);

    expect(runtime.isDesktop).toBe(false);
    expect(await runtime.getLaunchAtLogin()).toBe(false);
    expect(loadInvoke).not.toHaveBeenCalled();
  });

  test("desktop runtime invokes only the approved Tauri commands", async () => {
    const invoke = vi.fn(async (command: string) => command === "get_launch_at_login");
    const runtime = createDesktopRuntime({
      isDesktop: true,
      loadInvoke: async () => invoke as unknown as TauriInvoke,
    });

    await runtime.openMainWindow();
    expect(await runtime.getLaunchAtLogin()).toBe(true);
    await runtime.setLaunchAtLogin(true);
    await runtime.quit();

    expect(invoke.mock.calls).toEqual([
      ["open_main_window"],
      ["get_launch_at_login"],
      ["set_launch_at_login", { enabled: true }],
      ["quit_tailcam"],
    ]);
  });
});
