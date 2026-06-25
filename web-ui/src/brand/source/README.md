# Brand source vectors

The finalized TailCam logo, kept here as the source-of-truth vector masters
(transparent background):

- **`logo-full.svg`** — the complete lens-reticle mark.
- **`reticle.svg`** — the outer focus ring + ticks (boot-loader layer).
- **`aperture.svg`** — the inner iris + pupil (boot-loader layer).
- **`favicon.svg`** — the favicon master (shipped to `web-ui/public/favicon.svg`).

These are not bundled at runtime. The in-app mark (`../mark.tsx`) embeds the
`reticle` + `aperture` path data verbatim (split into layers so the boot
animation can move each independently, with per-instance gradient ids). The PWA
icons under `web-ui/public/` are rasterized from the matching app-icon PNG
exports. Keep this folder and `mark.tsx` in sync if the artwork changes.
