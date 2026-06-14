// Inline SVG logo + icon set (no external fetch). Ported from the prototype.
import type { ReactNode } from "react";

export interface IconProps {
  size?: number;
  className?: string;
  strokeWidth?: number;
}

interface BaseProps extends IconProps {
  children: ReactNode;
  viewBox?: string;
  stroke?: boolean;
  fill?: string;
}

const Icon = ({
  children,
  size = 22,
  className = "",
  viewBox = "0 0 24 24",
  stroke = true,
  fill = "none",
  strokeWidth = 1.7,
}: BaseProps) => (
  <svg
    width={size}
    height={size}
    viewBox={viewBox}
    fill={fill}
    stroke={stroke ? "currentColor" : "none"}
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden="true"
    focusable="false"
  >
    {children}
  </svg>
);

// TailCam mark — lens reticle.
export const Logo = ({ size = 28, className = "" }: { size?: number; className?: string }) => (
  <svg width={size} height={size} viewBox="0 0 48 48" fill="none" className={className} aria-hidden="true">
    <defs>
      <linearGradient id="tcg" x1="6" y1="6" x2="42" y2="42" gradientUnits="userSpaceOnUse">
        <stop stopColor="#5b7fff" />
        <stop offset="1" stopColor="#9b5cff" />
      </linearGradient>
    </defs>
    <circle cx="24" cy="24" r="19" stroke="url(#tcg)" strokeWidth="2.5" strokeDasharray="22 8" strokeDashoffset="11" strokeLinecap="round" />
    <path d="M24 1.5v5M24 41.5v5M1.5 24h5M41.5 24h5" stroke="url(#tcg)" strokeWidth="2.5" strokeLinecap="round" />
    <circle cx="24" cy="24" r="9" fill="url(#tcg)" />
    <circle cx="20.5" cy="20.5" r="2.6" fill="#dff0ff" opacity="0.9" />
  </svg>
);

export const IconGrid = (p: IconProps) => (
  <Icon {...p}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></Icon>
);
export const IconCamera = (p: IconProps) => (
  <Icon {...p}><path d="M3 8.5a2 2 0 0 1 2-2h2.2l1.2-1.8a1 1 0 0 1 .84-.45h5.5a1 1 0 0 1 .83.45L17 6.5h2a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><circle cx="12" cy="12.5" r="3.4" /></Icon>
);
export const IconFilm = (p: IconProps) => (
  <Icon {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" /></Icon>
);
export const IconSettings = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 13.5a7.8 7.8 0 0 0 0-3l1.7-1.3-1.7-3-2 .8a7.6 7.6 0 0 0-2.6-1.5L14.5 3h-5l-.3 2.3a7.6 7.6 0 0 0-2.6 1.5l-2-.8-1.7 3 1.7 1.3a7.8 7.8 0 0 0 0 3L1.9 14.8l1.7 3 2-.8a7.6 7.6 0 0 0 2.6 1.5L9.5 21h5l.3-2.3a7.6 7.6 0 0 0 2.6-1.5l2 .8 1.7-3z" /></Icon>
);
export const IconRefresh = (p: IconProps) => (
  <Icon {...p}><path d="M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5" /></Icon>
);
export const IconCamShutter = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5 16 12M20.5 12 11 11M12 20.5 8 12M3.5 12 13 13" /></Icon>
);
export const IconRecord = (p: IconProps) => <Icon {...p} fill="currentColor" stroke={false}><circle cx="12" cy="12" r="7" /></Icon>;
export const IconStop = (p: IconProps) => <Icon {...p} fill="currentColor" stroke={false}><rect x="6" y="6" width="12" height="12" rx="2" /></Icon>;
export const IconExpand = (p: IconProps) => (
  <Icon {...p}><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" /></Icon>
);
export const IconShrink = (p: IconProps) => (
  <Icon {...p}><path d="M4 9h3a2 2 0 0 0 2-2V4M15 4v3a2 2 0 0 0 2 2h3M20 15h-3a2 2 0 0 0-2 2v3M9 20v-3a2 2 0 0 0-2-2H4" /></Icon>
);
export const IconClose = (p: IconProps) => <Icon {...p}><path d="M6 6l12 12M18 6 6 18" /></Icon>;
export const IconChevL = (p: IconProps) => <Icon {...p}><path d="M15 5l-7 7 7 7" /></Icon>;
export const IconChevR = (p: IconProps) => <Icon {...p}><path d="M9 5l7 7-7 7" /></Icon>;
export const IconCopy = (p: IconProps) => <Icon {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></Icon>;
export const IconCheck = (p: IconProps) => <Icon {...p}><path d="M4 12.5 9 17.5 20 6.5" /></Icon>;
export const IconTrash = (p: IconProps) => (
  <Icon {...p}><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6M14 11v6" /></Icon>
);
export const IconDownload = (p: IconProps) => <Icon {...p}><path d="M12 3v12M7 10l5 5 5-5M5 21h14" /></Icon>;
export const IconMotion = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none" /><path d="M6.5 6.5a8 8 0 0 0 0 11M17.5 6.5a8 8 0 0 1 0 11M4 4a11.5 11.5 0 0 0 0 16M20 4a11.5 11.5 0 0 1 0 16" /></Icon>
);
export const IconZoom = (p: IconProps) => <Icon {...p}><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5M9 11h4M11 9v4" /></Icon>;
export const IconRotate = (p: IconProps) => <Icon {...p}><path d="M21 9a9 9 0 1 0 .5 4M21 4v5h-5" /></Icon>;
export const IconFlipH = (p: IconProps) => <Icon {...p}><path d="M12 3v18M7 8 4 12l3 4M17 8l3 4-3 4" /></Icon>;
export const IconFlipV = (p: IconProps) => <Icon {...p}><path d="M3 12h18M8 7 12 4l4 3M8 17l4 3 4-3" /></Icon>;
export const IconSun = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5 6.5 6.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" /></Icon>
);
export const IconContrast = (p: IconProps) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none" /></Icon>;
export const IconResolution = (p: IconProps) => <Icon {...p}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 9v6M7 9h3M7 15h3M14 9h3v6h-3" /></Icon>;
export const IconGlobe = (p: IconProps) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18" /></Icon>;
export const IconPhone = (p: IconProps) => <Icon {...p}><rect x="6" y="2.5" width="12" height="19" rx="2.5" /><path d="M10.5 18.5h3" /></Icon>;
export const IconFps = (p: IconProps) => <Icon {...p}><path d="M3 12a9 9 0 1 1 9 9" /><path d="M12 7v5l3 2" /><path d="M3 12H1.5M5 18l-1 1" /></Icon>;
export const IconWifi = (p: IconProps) => <Icon {...p}><path d="M5 12.5a10 10 0 0 1 14 0M8 15.5a6 6 0 0 1 8 0" /><circle cx="12" cy="19" r="1.1" fill="currentColor" stroke="none" /></Icon>;
export const IconWifiOff = (p: IconProps) => <Icon {...p}><path d="M3 4l18 18M9.5 16a4 4 0 0 1 5 0M5 12.5a10 10 0 0 1 5-2.7M19 12.5a10 10 0 0 0-3-2.3" /><circle cx="12" cy="19" r="1.1" fill="currentColor" stroke="none" /></Icon>;
export const IconDevice = (p: IconProps) => <Icon {...p}><rect x="2.5" y="5" width="13" height="10" rx="1.5" /><rect x="17" y="8" width="4.5" height="11" rx="1.2" /><path d="M6 19h6M9 15v4" /></Icon>;
export const IconHdd = (p: IconProps) => <Icon {...p}><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M7 14h.01M11 14h.01" /><path d="M3 11h18" /></Icon>;
export const IconPlay = (p: IconProps) => <Icon {...p} fill="currentColor" stroke={false}><path d="M8 5.5v13l11-6.5z" /></Icon>;
export const IconInfo = (p: IconProps) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5v.01" /></Icon>;
export const IconSliders = (p: IconProps) => <Icon {...p}><path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5" /><circle cx="16" cy="6" r="2" /><circle cx="8" cy="12" r="2" /><circle cx="13" cy="18" r="2" /></Icon>;
export const IconServer = (p: IconProps) => <Icon {...p}><rect x="3" y="4" width="18" height="7" rx="1.5" /><rect x="3" y="13" width="18" height="7" rx="1.5" /><path d="M7 7.5h.01M7 16.5h.01" /></Icon>;
export const IconWall = (p: IconProps) => <Icon {...p}><rect x="2.5" y="4" width="19" height="16" rx="1.5" /><path d="M2.5 12h19M9 4v8M16 12v8" /></Icon>;
export const IconSearch = (p: IconProps) => <Icon {...p}><circle cx="11" cy="11" r="7" /><path d="m21 21-4.5-4.5" /></Icon>;
export const IconClock = (p: IconProps) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" /></Icon>;
export const IconBolt = (p: IconProps) => <Icon {...p}><path d="M13 2 4.5 13.5h6L10.5 22 19 10.5h-6L13 2Z" /></Icon>;
export const IconChip = (p: IconProps) => <Icon {...p}><rect x="6" y="6" width="12" height="12" rx="1.5" /><rect x="9.5" y="9.5" width="5" height="5" rx="0.8" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></Icon>;
export const IconPause = (p: IconProps) => <Icon {...p} fill="currentColor" stroke={false}><path d="M7 4h3.5v16H7zM13.5 4H17v16h-3.5z" /></Icon>;
// Timelapse — a camcorder body with a clock hand (sped-up video over time).
export const IconTimelapse = (p: IconProps) => (
  <Icon {...p}><rect x="2.5" y="6.5" width="13" height="11" rx="2" /><path d="M15.5 10l6-2.5v9L15.5 14" /><path d="M9 9.7v2.6l1.8 1.1" /></Icon>
);
// Sparkle — post-processing / "smooth" enhancement.
export const IconSparkle = (p: IconProps) => (
  <Icon {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="M19 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z" /></Icon>
);
