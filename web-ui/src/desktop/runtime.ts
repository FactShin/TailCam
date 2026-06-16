export interface DesktopRuntime {
  isDesktop: boolean;
  openMainWindow(): Promise<void>;
  quit(): Promise<void>;
  getLaunchAtLogin(): Promise<boolean>;
  setLaunchAtLogin(enabled: boolean): Promise<void>;
}

export type TauriInvoke = <T = unknown>(command: string, args?: Record<string, unknown>) => Promise<T>;

interface DesktopRuntimeOptions {
  isDesktop?: boolean;
  loadInvoke?: () => Promise<TauriInvoke>;
}

function detectDesktop(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as unknown as Record<string, unknown>;
  return Boolean(w.__TAURI_INTERNALS__ || w.__TAURI__ || w.__TAURI_METADATA__);
}

async function loadTauriInvoke(): Promise<TauriInvoke> {
  const mod = await import("@tauri-apps/api/core");
  return mod.invoke as TauriInvoke;
}

export function createDesktopRuntime(options: DesktopRuntimeOptions = {}): DesktopRuntime {
  const isDesktop = options.isDesktop ?? detectDesktop();
  const loadInvoke = options.loadInvoke ?? loadTauriInvoke;
  let invokePromise: Promise<TauriInvoke> | null = null;

  const invoke = async <T = unknown>(
    command: "open_main_window" | "quit_tailcam" | "get_launch_at_login" | "set_launch_at_login",
    args?: Record<string, unknown>,
  ): Promise<T | undefined> => {
    if (!isDesktop) return undefined;
    invokePromise ??= loadInvoke();
    return invokePromise.then((fn) => (args === undefined ? fn<T>(command) : fn<T>(command, args)));
  };

  return {
    isDesktop,
    async openMainWindow() {
      await invoke("open_main_window");
    },
    async quit() {
      await invoke("quit_tailcam");
    },
    async getLaunchAtLogin() {
      return Boolean(await invoke<boolean>("get_launch_at_login"));
    },
    async setLaunchAtLogin(enabled: boolean) {
      await invoke("set_launch_at_login", { enabled });
    },
  };
}

export const desktopRuntime = createDesktopRuntime();
