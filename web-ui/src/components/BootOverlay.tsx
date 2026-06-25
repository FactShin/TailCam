import { useEffect, useState } from "react";

import { TailcamMark } from "../brand/mark";

/** Short lens-focus boot animation, shown once per browser session. */
export function BootOverlay() {
  const [show, setShow] = useState(() => {
    try { return !sessionStorage.getItem("tailcam.booted"); } catch { return true; }
  });

  useEffect(() => {
    if (!show) return;
    try { sessionStorage.setItem("tailcam.booted", "1"); } catch { /* ignore */ }
    const t = setTimeout(() => setShow(false), 2500);
    return () => clearTimeout(t);
  }, [show]);

  if (!show) return null;
  return (
    <div className="boot" aria-hidden="true">
      <div className="boot-inner">
        <span className="boot-mark"><TailcamMark animated size={112} /></span>
        <span className="boot-word">TailCam</span>
        <span className="boot-line">SYSTEMS NOMINAL</span>
        <span className="boot-bar"><i /></span>
      </div>
    </div>
  );
}
