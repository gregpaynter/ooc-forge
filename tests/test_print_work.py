from __future__ import annotations

import struct
import zlib

from forge.executor import PRINT_DPI, _png_dimensions, _tag_png_dpi


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def write_png(path, width: int, height: int) -> None:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + (b"\x00\x00\x00" * width)
    image = zlib.compress(row * height)
    path.write_bytes(signature + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", image) + _chunk(b"IEND", b""))


def test_print_master_png_is_tagged_300_dpi_without_changing_dimensions(tmp_path):
    path = tmp_path / "print-master.png"
    write_png(path, 16, 16)

    _tag_png_dpi(path, PRINT_DPI)

    assert _png_dimensions(path) == (16, 16)
    data = path.read_bytes()
    marker = data.index(b"pHYs")
    payload = data[marker + 4 : marker + 13]
    x_ppm, y_ppm, unit = struct.unpack(">IIB", payload)
    assert unit == 1
    assert x_ppm == y_ppm == 11811
    # The default 1024px SDXL Study becomes a 4096px 4x Work master.
    assert round((4096 / PRINT_DPI) * 25.4, 1) == 346.8


def test_retagging_png_replaces_existing_phys_chunk(tmp_path):
    path = tmp_path / "print-master.png"
    write_png(path, 32, 24)
    _tag_png_dpi(path, 150)
    _tag_png_dpi(path, 300)

    assert path.read_bytes().count(b"pHYs") == 1
    assert _png_dimensions(path) == (32, 24)
