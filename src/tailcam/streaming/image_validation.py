"""Bound raster dimensions before asking a native decoder to allocate pixels."""

from __future__ import annotations

import struct


def raster_dimensions(data: bytes, *, max_pixels: int = 4096 * 4096) -> tuple[int, int]:
    dimensions = []
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 33 or data[8:16] != b"\x00\x00\x00\rIHDR":
            raise ValueError("Invalid PNG header")
        dimensions.append(struct.unpack(">II", data[16:24]))
    elif data.startswith(b"\xff\xd8"):
        offset = 2
        # SOF markers with dimensions. JPEG control/table markers are excluded.
        starts = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset < len(data):
            if data[offset] != 0xFF:
                raise ValueError("Invalid JPEG marker")
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xDA, 0xD9}:
                break
            if marker in {0x01, *range(0xD0, 0xD9)}:
                continue
            if offset + 2 > len(data):
                raise ValueError("Truncated JPEG marker")
            length = int.from_bytes(data[offset : offset + 2], "big")
            if length < 2 or offset + length > len(data):
                raise ValueError("Invalid JPEG marker length")
            if marker in starts:
                if length < 8:
                    raise ValueError("Invalid JPEG frame header")
                height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
                dimensions.append((width, height))
            offset += length
    else:
        raise ValueError("Only JPEG and PNG input is supported")
    if not dimensions or any(
        width < 1 or height < 1 or width * height > max_pixels for width, height in dimensions
    ):
        raise ValueError("Image dimensions exceed their limit")
    if any(item != dimensions[0] for item in dimensions):
        raise ValueError("Conflicting image dimensions")
    return dimensions[0]
