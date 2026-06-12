import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

import { IconClose } from "../icons";

type Kind = "info" | "ok" | "err";
interface ToastItem {
  id: number;
  msg: string;
  kind: Kind;
  action?: { label: string; fn: () => void };
}
interface ToastApi {
  info: (m: string, o?: Partial<ToastOpts>) => void;
  ok: (m: string, o?: Partial<ToastOpts>) => void;
  err: (m: string, o?: Partial<ToastOpts>) => void;
}
interface ToastOpts {
  duration: number;
  action: { label: string; fn: () => void };
}

const ToastCtx = createContext<ToastApi | null>(null);
let _id = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const remove = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (msg: string, kind: Kind, opts?: Partial<ToastOpts>) => {
      const id = _id++;
      setToasts((t) => [...t, { id, msg, kind, action: opts?.action }]);
      setTimeout(() => remove(id), opts?.duration ?? 3200);
    },
    [remove],
  );
  const api: ToastApi = {
    info: (m, o) => push(m, "info", o),
    ok: (m, o) => push(m, "ok", o),
    err: (m, o) => push(m, "err", o),
  };
  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="toast-wrap" role="region" aria-label="Notifications">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} role="status">
            <span className="toast-dot" />
            <span className="toast-msg">{t.msg}</span>
            {t.action && (
              <button className="toast-action" onClick={() => { t.action!.fn(); remove(t.id); }}>
                {t.action.label}
              </button>
            )}
            <button className="toast-x" aria-label="Dismiss" onClick={() => remove(t.id)}>
              <IconClose size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
