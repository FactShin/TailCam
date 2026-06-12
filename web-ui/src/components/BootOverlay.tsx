import { useEffect, useState } from "react";

import { Logo } from "../icons";

/** Short "systems nominal" boot sweep, shown once per browser session. */
export function BootOverlay() {
  const [show, setShow] = useState(() => {
    try { return !sessionStorage.getItem("tailcam.booted"); } catch { return true; }
  });

  useEffect(() => {
    if (!show) return;
    try { sessionStorage.setItem("tailcam.booted", "1"); } catch { /* ignore */ }
    const t = setTimeout(() => setShow(false), 2100);
    return () => clearTimeout(t);
  }, [show]);

  if (!show) return null;
  return (
    <div className="boot" aria-hidden="true">
      <div className="boot-inner">
        <span className="boot-logo"><Logo size={56} /></span>
        <span className="boot-line">TAILCAM · SYSTEMS NOMINAL</span>
        <span className="boot-bar"><i /></span>
      </div>
    </div>
  );
}
