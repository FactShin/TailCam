import { useId } from "react";

/**
 * TailCam logo mark — a camera-lens / iris reticle.
 *
 * The artwork is the designer's final master, kept as verbatim SVG fragments so
 * it renders pixel-identical to the favicon / PWA icon. It is split into two
 * overlaid layers — the outer `reticle` (focus ring + ticks) and the inner
 * `aperture` (iris + pupil) — so the boot loader can animate each independently.
 *
 * Gradient ids are namespaced per instance (via `useId`) so multiple marks can
 * coexist on the page without `url(#id)` collisions.
 */

const DEFS = `
<linearGradient id="grad" gradientUnits="userSpaceOnUse" x1="84" y1="206" x2="430" y2="306"><stop offset="0" stop-color="#0E74FF"/><stop offset="0.5" stop-color="#5A38F2"/><stop offset="1" stop-color="#9C20EE"/></linearGradient>
<radialGradient id="lensShade" gradientUnits="userSpaceOnUse" cx="214" cy="214" r="134"><stop offset="0.62" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#08052a" stop-opacity="0.18"/></radialGradient>
<radialGradient id="pupilG" gradientUnits="userSpaceOnUse" cx="242" cy="242" r="64"><stop offset="0" stop-color="#251d57"/><stop offset="1" stop-color="#120c33"/></radialGradient>`;

const RETICLE = `<g fill="none" stroke="url(#grad)" stroke-width="27" stroke-linecap="round"><path d="M 413.875 307.297 A 166 166 0 0 1 307.297 413.875"/><path d="M 204.703 413.875 A 166 166 0 0 1 98.125 307.297"/><path d="M 98.125 204.703 A 166 166 0 0 1 204.703 98.125"/><path d="M 307.297 98.125 A 166 166 0 0 1 413.875 204.703"/><path d="M 394 256 L 450 256"/><path d="M 256 394 L 256 450"/><path d="M 118 256 L 62 256"/><path d="M 256 118 L 256 62"/></g>`;

const APERTURE = `<circle cx="256" cy="256" r="120" fill="url(#grad)"/><path d="M 279.84 138.392 A 120 120 0 0 1 362.814 201.312 Q 301.138 199.906 261.554 199.271 A 57 57 0 0 0 215.111 216.288 Q 240.287 185.735 279.84 138.392 Z" fill="url(#grad)"/><path d="M 279.84 138.392 A 120 120 0 0 1 362.814 201.312 Q 301.138 199.906 261.554 199.271 A 57 57 0 0 0 215.111 216.288 Q 240.287 185.735 279.84 138.392 Z" fill="#fff" fill-opacity="0.171"/><path d="M 362.814 201.312 A 120 120 0 0 1 365.354 305.413 Q 327.999 256.317 303.815 224.973 A 57 57 0 0 0 261.554 199.271 Q 301.138 199.906 362.814 201.312 Z" fill="url(#grad)"/><path d="M 365.354 305.413 A 120 120 0 0 1 285.549 372.305 Q 300.643 312.489 310.07 274.038 A 57 57 0 0 0 303.815 224.973 Q 327.999 256.317 365.354 305.413 Z" fill="url(#grad)"/><path d="M 365.354 305.413 A 120 120 0 0 1 285.549 372.305 Q 300.643 312.489 310.07 274.038 A 57 57 0 0 0 303.815 224.973 Q 327.999 256.317 365.354 305.413 Z" fill="#0a0730" fill-opacity="0.093"/><path d="M 285.549 372.305 A 120 120 0 0 1 183.492 351.617 Q 239.67 326.124 275.609 309.521 A 57 57 0 0 0 310.07 274.038 Q 300.643 312.489 285.549 372.305 Z" fill="url(#grad)"/><path d="M 285.549 372.305 A 120 120 0 0 1 183.492 351.617 Q 239.67 326.124 275.609 309.521 A 57 57 0 0 0 310.07 274.038 Q 300.643 312.489 285.549 372.305 Z" fill="#0a0730" fill-opacity="0.116"/><path d="M 183.492 351.617 A 120 120 0 0 1 136.036 258.928 Q 190.993 286.954 226.382 304.701 A 57 57 0 0 0 275.609 309.521 Q 239.67 326.124 183.492 351.617 Z" fill="url(#grad)"/><path d="M 183.492 351.617 A 120 120 0 0 1 136.036 258.928 Q 190.993 286.954 226.382 304.701 A 57 57 0 0 0 275.609 309.521 Q 239.67 326.124 183.492 351.617 Z" fill="#0a0730" fill-opacity="0.052"/><path d="M 136.036 258.928 A 120 120 0 0 1 178.915 164.034 Q 191.268 224.475 199.458 263.208 A 57 57 0 0 0 226.382 304.701 Q 190.993 286.954 136.036 258.928 Z" fill="url(#grad)"/><path d="M 136.036 258.928 A 120 120 0 0 1 178.915 164.034 Q 191.268 224.475 199.458 263.208 A 57 57 0 0 0 226.382 304.701 Q 190.993 286.954 136.036 258.928 Z" fill="#fff" fill-opacity="0.095"/><path d="M 178.915 164.034 A 120 120 0 0 1 279.84 138.392 Q 240.287 185.735 215.111 216.288 A 57 57 0 0 0 199.458 263.208 Q 191.268 224.475 178.915 164.034 Z" fill="url(#grad)"/><path d="M 178.915 164.034 A 120 120 0 0 1 279.84 138.392 Q 240.287 185.735 215.111 216.288 A 57 57 0 0 0 199.458 263.208 Q 191.268 224.475 178.915 164.034 Z" fill="#fff" fill-opacity="0.213"/><circle cx="256" cy="256" r="120" fill="url(#lensShade)"/>`;

const PUPIL = `<circle cx="256" cy="256" r="57" fill="url(#pupilG)"/><circle cx="233.2" cy="233.2" r="8.835" fill="#fff"/>`;

/** Namespace the four gradient ids so instances don't collide. */
function ns(svg: string, p: string): string {
  return svg.replace(/(id="|url\(#)(grad|lensShade|pupilG)/g, (_m, pre, id) => `${pre}${p}${id}`);
}

export interface MarkProps {
  size?: number;
  className?: string;
  /** Adds the boot-loader animation classes (see styles.css `.tcmark-anim`). */
  animated?: boolean;
}

export function TailcamMark({ size = 28, className = "", animated = false }: MarkProps) {
  const p = `tc${useId().replace(/[^a-zA-Z0-9]/g, "")}_`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 512 512"
      fill="none"
      className={`tcmark ${animated ? "tcmark-anim" : ""} ${className}`.trim()}
      aria-hidden="true"
    >
      <defs dangerouslySetInnerHTML={{ __html: ns(DEFS, p) }} />
      <g className="tcmark-reticle" dangerouslySetInnerHTML={{ __html: ns(RETICLE, p) }} />
      <g className="tcmark-aperture">
        <g className="tcmark-iris" dangerouslySetInnerHTML={{ __html: ns(APERTURE, p) }} />
        <g className="tcmark-pupil" dangerouslySetInnerHTML={{ __html: ns(PUPIL, p) }} />
      </g>
    </svg>
  );
}
