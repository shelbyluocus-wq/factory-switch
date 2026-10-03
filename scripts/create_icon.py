"""Render the titlebar's rounded stripe mark as PNG and multi-size Windows ICO."""
import math
from pathlib import Path
import struct
import zlib


def pixels(size):
    result = bytearray()
    for y in range(size):
        for x in range(size):
            covered = 0
            for sy in range(4):
                for sx in range(4):
                    u = (x + (sx + .5) / 4) * 18 / size
                    v = (y + (sy + .5) / 4) * 18 / size
                    dx, dy = max(abs(u - 9) - 5, 0), max(abs(v - 9) - 5, 0)
                    if dx * dx + dy * dy <= 16 and (u + v) / math.sqrt(2) % 4 < 2:
                        covered += 1
            result.extend((182, 172, 207, round(255 * covered / 16)))
    return bytes(result)


def png(size, rgba):
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
    rows = b''.join(b'\0' + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


def dib(size, rgba):
    color = bytearray()
    mask = bytearray()
    stride = ((size + 31) // 32) * 4
    for y in reversed(range(size)):
        row = bytearray(stride)
        for x in range(size):
            r, g, b, a = rgba[(y * size + x) * 4:(y * size + x + 1) * 4]
            color.extend((b, g, r, a))
            if a == 0:
                row[x // 8] |= 1 << (7 - x % 8)
        mask.extend(row)
    return struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, len(color), 0, 0, 0, 0) + color + mask


def main():
    root = Path(__file__).resolve().parent.parent / 'ui'
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    frames = []
    for size in sizes:
        rgba = pixels(size)
        frames.append(png(size, rgba) if size == 256 else dib(size, rgba))
    (root / 'app-icon.png').write_bytes(frames[-1])
    offset = 6 + 16 * len(sizes)
    directory = bytearray(struct.pack('<HHH', 0, 1, len(sizes)))
    for size, frame in zip(sizes, frames):
        directory.extend(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    (root / 'app-icon.ico').write_bytes(directory + b''.join(frames))


if __name__ == '__main__':
    main()
