/**
 * format.js — small pure formatting helpers shared by popup, background,
 * glance and options. No WebExtension APIs here.
 */

/**
 * Human relative time for an epoch-seconds timestamp ("2m ago").
 * @param {number} tsSeconds epoch seconds (TailCam timestamps are float seconds)
 * @param {number} [nowMs] current time in ms (default Date.now())
 * @returns {string}
 */
export function relativeTime(tsSeconds, nowMs = Date.now()) {
  const diff = Math.max(0, Math.floor(nowMs / 1000 - tsSeconds));
  if (diff < 10) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(tsSeconds * 1000).toLocaleDateString();
}

/** Label keyword -> emoji chip groups (matched as substrings, lowercase). */
const LABEL_CHIPS = [
  { emoji: "\u{1F9CD}", words: ["person", "people", "human", "face"] },
  {
    emoji: "\u{1F43E}",
    words: ["animal", "cat", "dog", "pet", "bird", "wildlife", "fox", "deer"],
  },
  {
    emoji: "\u{1F697}",
    words: ["vehicle", "car", "truck", "bus", "bike", "motorcycle", "van"],
  },
  { emoji: "\u{1F4E6}", words: ["package", "parcel", "delivery", "box"] },
];

/**
 * Emoji chip for an AI event label. Unknown/empty labels get a neutral dot.
 * @param {string|null|undefined} label MotionEventInfo.label
 * @returns {string} single emoji / glyph
 */
export function labelChip(label) {
  if (!label) return "●"; // ●
  const l = String(label).toLowerCase();
  for (const { emoji, words } of LABEL_CHIPS) {
    if (words.some((w) => l.includes(w))) return emoji;
  }
  return "●";
}

/**
 * Parse "HH:MM" into minutes since midnight, or null when malformed.
 * @param {string} hhmm
 * @returns {number|null}
 */
function parseHHMM(hhmm) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm ?? "").trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

/**
 * Whether `now` falls inside the quiet-hours window [start, end), local time.
 * Handles overnight ranges (e.g. 22:00 -> 07:00). An equal or malformed
 * start/end means quiet hours are effectively disabled (returns false).
 * @param {Date} now
 * @param {string} start "HH:MM"
 * @param {string} end "HH:MM"
 * @returns {boolean}
 */
export function isInQuietHours(now, start, end) {
  const s = parseHHMM(start);
  const e = parseHHMM(end);
  if (s === null || e === null || s === e) return false;
  const m = now.getHours() * 60 + now.getMinutes();
  return s < e ? m >= s && m < e : m >= s || m < e;
}
