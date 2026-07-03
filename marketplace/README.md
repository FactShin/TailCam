# TailCam Plugin Marketplace

This folder is the **curated plugin registry** that every TailCam node's
**Plugins** page reads. Each entry in [`index.json`](index.json) points at a
single-file Python plugin in [`plugins/`](plugins/) and pins its **sha256** —
TailCam verifies the checksum (plus size and syntax) before a plugin ever
touches disk.

- **Users**: you never need this folder directly — open **Plugins** in the
  TailCam UI and click Install.
- **Self-hosting**: point `plugins.registry_url` in `config.toml` at your own
  copy of `index.json` to run a private registry.

## Publish your plugin

1. Write a single-file plugin (start from [`TEMPLATE.py`](TEMPLATE.py), or copy
   [`plugins/event_logger.py`](plugins/event_logger.py)). The full authoring
   guide is in the in-app **Docs → Plugins** page.
2. Declare metadata in a module-level `__plugin__` dict (`id` must equal the
   file stem) and keep everything in **one `.py` file under 1 MB**, importing
   only the Python stdlib, TailCam itself, and TailCam's dependencies
   (`httpx`, `cv2`, `numpy`).
3. Add the file to `plugins/`, run `python marketplace/build_index.py`, and
   open a **pull request** with both the plugin and the regenerated
   `index.json`.

## Review criteria (what maintainers check)

Plugins run with the full privileges of the TailCam process — there is no
sandbox — so review is the security boundary. A submission must be:

- **Readable** — no obfuscation, no encoded blobs, no `eval`/`exec` of dynamic
  content, no downloading further code at runtime.
- **Scoped** — network calls only to the service the plugin integrates
  (declared in its docstring); no credential harvesting; secrets come from the
  user's own `[plugins.settings.*]` table.
- **Resilient** — every hook catches its own exceptions; a dead third-party
  service must never break detection or notifications.
- **Honest** — description and `settings_example` match what the code does.

Version bumps go through the same review; the index pins a new sha256 with
each release, and TailCam shows users an Update button.
