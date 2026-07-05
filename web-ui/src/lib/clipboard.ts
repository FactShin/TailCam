/** Copy text to the clipboard, robust to insecure (http://) contexts.
 *
 * The async Clipboard API (`navigator.clipboard`) only exists in secure
 * contexts. TailCam is routinely reached over plain http:// (e.g.
 * http://<tailnet-ip>:8088 when Tailscale Serve/HTTPS isn't fronting the node),
 * where `navigator.clipboard` is `undefined` — so we fall back to a hidden
 * <textarea> + `document.execCommand("copy")`, which works without HTTPS.
 * Returns whether the copy succeeded so callers can toast accordingly.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through to the execCommand path (e.g. permission denied) */
    }
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    // Keep it out of view and out of the layout/scroll.
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.setAttribute("readonly", "");
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
