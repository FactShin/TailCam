import type { CameraInfo } from "../types";

// Camera ids can contain slashes; encode host + id as two route segments so
// the same id on two different hosts stays distinct.
export const cameraPath = (cam: Pick<CameraInfo, "host" | "id">) =>
  `/camera/${encodeURIComponent(cam.host)}/${encodeURIComponent(cam.id)}`;
