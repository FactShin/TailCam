// icons.jsx — inline SVG logo + icon set (no external fetch)
// All icons take {size, className, strokeWidth} and inherit currentColor.

const Icon = ({ children, size = 22, className = "", viewBox = "0 0 24 24", stroke = true, fill = "none", strokeWidth = 1.7 }) => (
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

// TailCam logo — camera aperture / eye glyph
const Logo = ({ size = 28, className = "" }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className} aria-hidden="true">
    <defs>
      <linearGradient id="acg" x1="4" y1="4" x2="28" y2="28" gradientUnits="userSpaceOnUse">
        <stop stopColor="#6ba2ff" />
        <stop offset="1" stopColor="#4f8cff" />
      </linearGradient>
    </defs>
    <circle cx="16" cy="16" r="13" stroke="url(#acg)" strokeWidth="2.2" />
    {/* aperture blades */}
    <g stroke="url(#acg)" strokeWidth="2.2" strokeLinecap="round">
      <path d="M16 5.5 L21.5 15 L10.5 15 Z" fill="rgba(79,140,255,0.18)" stroke="none" />
      <path d="M26.5 16 L18 21.5 L18 11 Z" fill="rgba(79,140,255,0.10)" stroke="none" />
      <path d="M5.5 16 L14 11 L14 21.5 Z" fill="rgba(79,140,255,0.10)" stroke="none" />
    </g>
    <circle cx="16" cy="16" r="4.4" fill="#4f8cff" />
    <circle cx="14.4" cy="14.4" r="1.4" fill="#cfe0ff" />
  </svg>
);

const LogoMark = Logo;

const IconGrid = (p) => <Icon {...p}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></Icon>;
const IconCamera = (p) => <Icon {...p}><path d="M3 8.5a2 2 0 0 1 2-2h2.2l1.2-1.8a1 1 0 0 1 .84-.45h5.5a1 1 0 0 1 .83.45L17 6.5h2a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><circle cx="12" cy="12.5" r="3.4" /></Icon>;
const IconFilm = (p) => <Icon {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" /></Icon>;
const IconActivity = (p) => <Icon {...p}><path d="M3 12h3.5l2.5-7 4 14 2.5-7H21" /></Icon>;
const IconSettings = (p) => <Icon {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 13.5a7.8 7.8 0 0 0 0-3l1.7-1.3-1.7-3-2 .8a7.6 7.6 0 0 0-2.6-1.5L14.5 3h-5l-.3 2.3a7.6 7.6 0 0 0-2.6 1.5l-2-.8-1.7 3 1.7 1.3a7.8 7.8 0 0 0 0 3L1.9 14.8l1.7 3 2-.8a7.6 7.6 0 0 0 2.6 1.5L9.5 21h5l.3-2.3a7.6 7.6 0 0 0 2.6-1.5l2 .8 1.7-3z" /></Icon>;
const IconRefresh = (p) => <Icon {...p}><path d="M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5" /></Icon>;
const IconCircle = (p) => <Icon {...p} fill="currentColor" stroke={false}><circle cx="12" cy="12" r="6" /></Icon>;
const IconCamShutter = (p) => <Icon {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5 16 12M20.5 12 11 11M12 20.5 8 12M3.5 12 13 13" /></Icon>;
const IconRecord = (p) => <Icon {...p} fill="currentColor" stroke={false}><circle cx="12" cy="12" r="7" /></Icon>;
const IconStop = (p) => <Icon {...p} fill="currentColor" stroke={false}><rect x="6" y="6" width="12" height="12" rx="2" /></Icon>;
const IconExpand = (p) => <Icon {...p}><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" /></Icon>;
const IconShrink = (p) => <Icon {...p}><path d="M4 9h3a2 2 0 0 0 2-2V4M15 4v3a2 2 0 0 0 2 2h3M20 15h-3a2 2 0 0 0-2 2v3M9 20v-3a2 2 0 0 0-2-2H4" /></Icon>;
const IconClose = (p) => <Icon {...p}><path d="M6 6l12 12M18 6 6 18" /></Icon>;
const IconChevL = (p) => <Icon {...p}><path d="M15 5l-7 7 7 7" /></Icon>;
const IconChevR = (p) => <Icon {...p}><path d="M9 5l7 7-7 7" /></Icon>;
const IconChevDown = (p) => <Icon {...p}><path d="M5 9l7 7 7-7" /></Icon>;
const IconChevUp = (p) => <Icon {...p}><path d="M5 15l7-7 7 7" /></Icon>;
const IconCopy = (p) => <Icon {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></Icon>;
const IconCheck = (p) => <Icon {...p}><path d="M4 12.5 9 17.5 20 6.5" /></Icon>;
const IconTrash = (p) => <Icon {...p}><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6M14 11v6" /></Icon>;
const IconDownload = (p) => <Icon {...p}><path d="M12 3v12M7 10l5 5 5-5M5 21h14" /></Icon>;
const IconMotion = (p) => <Icon {...p}><circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none" /><path d="M6.5 6.5a8 8 0 0 0 0 11M17.5 6.5a8 8 0 0 1 0 11M4 4a11.5 11.5 0 0 0 0 16M20 4a11.5 11.5 0 0 1 0 16" /></Icon>;
const IconZoom = (p) => <Icon {...p}><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5M9 11h4M11 9v4" /></Icon>;
const IconRotate = (p) => <Icon {...p}><path d="M21 9a9 9 0 1 0 .5 4M21 4v5h-5" /></Icon>;
const IconFlipH = (p) => <Icon {...p}><path d="M12 3v18M7 8 4 12l3 4M17 8l3 4-3 4" /></Icon>;
const IconFlipV = (p) => <Icon {...p}><path d="M3 12h18M8 7 12 4l4 3M8 17l4 3 4-3" /></Icon>;
const IconSun = (p) => <Icon {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5 6.5 6.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" /></Icon>;
const IconContrast = (p) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none" /></Icon>;
const IconDrop = (p) => <Icon {...p}><path d="M12 3s6 6.5 6 10.5a6 6 0 0 1-12 0C6 9.5 12 3 12 3z" /></Icon>;
const IconResolution = (p) => <Icon {...p}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 9v6M7 9h3M7 15h3M14 9h3v6h-3" /></Icon>;
const IconGlobe = (p) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18" /></Icon>;
const IconPhone = (p) => <Icon {...p}><rect x="6" y="2.5" width="12" height="19" rx="2.5" /><path d="M10.5 18.5h3" /></Icon>;
const IconFps = (p) => <Icon {...p}><path d="M3 12a9 9 0 1 1 9 9" /><path d="M12 7v5l3 2" /><path d="M3 12H1.5M5 18l-1 1" /></Icon>;
const IconWifi = (p) => <Icon {...p}><path d="M5 12.5a10 10 0 0 1 14 0M8 15.5a6 6 0 0 1 8 0" /><circle cx="12" cy="19" r="1.1" fill="currentColor" stroke="none" /></Icon>;
const IconWifiOff = (p) => <Icon {...p}><path d="M3 4l18 18M9.5 16a4 4 0 0 1 5 0M5 12.5a10 10 0 0 1 5-2.7M19 12.5a10 10 0 0 0-3-2.3" /><circle cx="12" cy="19" r="1.1" fill="currentColor" stroke="none" /></Icon>;
const IconDevice = (p) => <Icon {...p}><rect x="2.5" y="5" width="13" height="10" rx="1.5" /><rect x="17" y="8" width="4.5" height="11" rx="1.2" /><path d="M6 19h6M9 15v4" /></Icon>;
const IconHdd = (p) => <Icon {...p}><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M7 14h.01M11 14h.01" /><path d="M3 11h18" /></Icon>;
const IconImage = (p) => <Icon {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8.5" cy="9.5" r="1.8" /><path d="M21 16l-5-4-7 6" /></Icon>;
const IconPlay = (p) => <Icon {...p} fill="currentColor" stroke={false}><path d="M8 5.5v13l11-6.5z" /></Icon>;
const IconBars = (p) => <Icon {...p}><path d="M4 6h16M4 12h16M4 18h16" /></Icon>;
const IconInfo = (p) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5v.01" /></Icon>;
const IconSliders = (p) => <Icon {...p}><path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5" /><circle cx="16" cy="6" r="2" fill="var(--panel)" /><circle cx="8" cy="12" r="2" fill="var(--panel)" /><circle cx="13" cy="18" r="2" fill="var(--panel)" /></Icon>;

Object.assign(window, {
  Logo, LogoMark, IconGrid, IconCamera, IconFilm, IconActivity, IconSettings, IconRefresh,
  IconCircle, IconCamShutter, IconRecord, IconStop, IconExpand, IconShrink, IconClose,
  IconChevL, IconChevR, IconChevDown, IconChevUp, IconCopy, IconCheck, IconTrash, IconDownload,
  IconMotion, IconZoom, IconRotate, IconFlipH, IconFlipV, IconSun, IconContrast, IconDrop,
  IconResolution, IconGlobe, IconPhone, IconFps, IconWifi, IconWifiOff, IconDevice, IconHdd,
  IconImage, IconPlay, IconBars, IconInfo, IconSliders,
});
