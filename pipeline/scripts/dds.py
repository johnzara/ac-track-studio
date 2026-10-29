"""Mipmapped DDS textures (no conversion at load; Assetto Corsa reads DXT1 / DXT5):

  colour            DXT1 (BC1)
  colour + alpha    DXT5 (BC3)
  normal map        ATI2 (BC5)       X in red, Y in green (OpenGL / Y+)

Pillow encodes each mip level; this module writes one legacy DDS header with the whole chain.
"""

import io
import struct

from PIL import Image

FOURCC = {"DXT1": b"DXT1", "DXT5": b"DXT5", "BC5": b"ATI2"}
BLOCK_BYTES = {"DXT1": 8, "DXT5": 16, "BC5": 16}


def _encode(im, fmt):
    b = io.BytesIO()
    im.save(b, format="DDS", pixel_format=fmt)
    data = b.getvalue()
    head = 128 + (20 if data[84:88] == b"DX10" else 0)
    return data[head:]


def write(img, path, fmt="DXT1", min_size=4, straight_alpha=False):
    """Save `img` (PIL) as a DDS with mipmaps down to `min_size` px. straight_alpha: build the
    mips channel by channel (alpha holds data such as a mask; Pillow otherwise resizes RGBA
    premultiplied, losing the colour wherever alpha is 0)."""
    if fmt == "DXT5":
        im = img.convert("RGBA")
    else:
        im = img.convert("RGB")
    w, h = im.size
    levels = []
    while True:
        levels.append(_encode(im, fmt))
        if min(im.size) <= min_size:
            break
        size = (max(1, im.width // 2), max(1, im.height // 2))
        if straight_alpha and im.mode == "RGBA":
            im = Image.merge("RGBA", [b.resize(size, Image.LANCZOS) for b in im.split()])
        else:
            im = im.resize(size, Image.LANCZOS)
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000   # CAPS HEIGHT WIDTH PIXELFORMAT MIPMAPCOUNT LINEARSIZE
    linear = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK_BYTES[fmt]
    header = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, h, w, linear, 0, len(levels))
    header += b"\0" * 44                                         # reserved
    header += struct.pack("<II4sIIIII", 32, 0x4, FOURCC[fmt], 0, 0, 0, 0, 0)
    header += struct.pack("<IIIII", 0x1000 | 0x400000 | 0x8, 0, 0, 0, 0)  # TEXTURE MIPMAP COMPLEX
    with open(path, "wb") as f:
        f.write(header)
        for lv in levels:
            f.write(lv)
    return path
