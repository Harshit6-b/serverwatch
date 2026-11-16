"""Turn the PGM frames from render_screens into OLED-looking PNGs (scaled, glowing pixels).
python3 oled_png.py in_dir out_dir [scale]"""
import pathlib
import sys
import zlib
import struct


def read_pgm(p):
    data = p.read_bytes()
    parts = data.split(b"\n", 3)
    w, h = map(int, parts[1].split())
    return w, h, parts[3]


def write_png(path, w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main():
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    dst.mkdir(parents=True, exist_ok=True)
    on, off, bezel, pad = (130, 220, 255), (8, 14, 22), (20, 24, 30), 3 * k
    for p in sorted(src.glob("*.pgm")):
        w, h, px = read_pgm(p)
        W, H = w * k + 2 * pad, h * k + 2 * pad
        rows = []
        for Y in range(H):
            row = []
            for X in range(W):
                x, y = (X - pad) // k, (Y - pad) // k
                if not (0 <= x < w and 0 <= y < h):
                    row += bezel
                    continue
                lit = px[y * w + x] > 0
                gap = (X - pad) % k == k - 1 or (Y - pad) % k == k - 1   # pixel grid like a real OLED
                row += (off if not lit else (tuple(c * 3 // 4 for c in on) if gap else on))
            rows.append(row)
        out = dst / (p.stem + ".png")
        write_png(out, W, H, rows)
        print(out)


if __name__ == "__main__":
    main()
