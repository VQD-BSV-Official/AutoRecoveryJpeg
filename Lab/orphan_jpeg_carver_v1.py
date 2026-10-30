#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orphan_jpeg_carver.py - Tìm và khôi phục mảnh JPEG "mồ côi" (không còn header).

Dựa trên ý tưởng của Uzun & Sencar:
  - "Carving Orphaned JPEG File Fragments" (IEEE TIFS 2015)
  - "JpgScraper: An Advanced Carver for JPEG Files" (IEEE TIFS 2019)
Đây là bản cài đặt lại độc lập (không dùng mã nguồn của họ), chỉ hỗ trợ JPEG
baseline (Huffman, tuần tự). Chỉ dùng trên dữ liệu bạn có quyền xử lý.

Quy trình:
  1. Quét ảnh đĩa/tệp theo khối, giữ lại khối trông giống dữ liệu entropy JPEG:
     sau mỗi byte 0xFF chỉ được là 0x00, RSTn (D0-D7) hoặc EOI (D9).
  2. Tìm thêm header SOS còn sót (FFDA + cấu trúc hợp lệ) để biết chỗ bắt đầu chính xác.
  3. Với mỗi mảnh: thử các bảng Huffman (chuẩn hoặc lấy từ file tham chiếu),
     các kiểu lấy mẫu màu và pha MCU, dò vị trí bit đồng bộ nhờ tính tự đồng bộ
     của mã Huffman (giải mã liền >= N khối mà không lỗi).
  4. IDCT, dò độ rộng ảnh bằng độ liên tục giữa các hàng khối, sửa dịch vòng ngang,
     kéo giãn độ sáng/màu (vì DC chỉ còn là giá trị tương đối) rồi lưu PNG.

Cách dùng:
  python orphan_jpeg_carver.py disk.img -o out
  python orphan_jpeg_carver.py raw_fragment.bin --raw -o out
  python orphan_jpeg_carver.py disk.img --ref anh_tot_cung_may.jpg -o out
"""
import argparse
import os
import re
import struct
import sys
import mmap
from array import array

import numpy as np
from PIL import Image

# ----------------------------------------------------------------------------
# Hằng số JPEG
# ----------------------------------------------------------------------------
ZZ = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40,
      48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29,
      22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54,
      47, 55, 62, 63]

STD_QL = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
          14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
          18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
          49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
STD_QC = [17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99,
          24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99] + [99] * 32

_H = lambda s: [int(x, 16) for x in s.split()]
STD_DC_BITS_L = [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
STD_DC_BITS_C = [0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
STD_DC_VALS = list(range(12))
STD_AC_BITS_L = [0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 125]
STD_AC_VALS_L = _H(
    "01 02 03 00 04 11 05 12 21 31 41 06 13 51 61 07 22 71 14 32 81 91 a1 08 23 42 b1 c1 15 52 d1 f0 "
    "24 33 62 72 82 09 0a 16 17 18 19 1a 25 26 27 28 29 2a 34 35 36 37 38 39 3a 43 44 45 46 47 48 49 "
    "4a 53 54 55 56 57 58 59 5a 63 64 65 66 67 68 69 6a 73 74 75 76 77 78 79 7a 83 84 85 86 87 88 89 "
    "8a 92 93 94 95 96 97 98 99 9a a2 a3 a4 a5 a6 a7 a8 a9 aa b2 b3 b4 b5 b6 b7 b8 b9 ba c2 c3 c4 c5 "
    "c6 c7 c8 c9 ca d2 d3 d4 d5 d6 d7 d8 d9 da e1 e2 e3 e4 e5 e6 e7 e8 e9 ea f1 f2 f3 f4 f5 f6 f7 f8 f9 fa")
STD_AC_BITS_C = [0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 119]
STD_AC_VALS_C = _H(
    "00 01 02 03 11 04 05 21 31 06 12 41 51 07 61 71 13 22 32 81 08 14 42 91 a1 b1 c1 09 23 33 52 f0 "
    "15 62 72 d1 0a 16 24 34 e1 25 f1 17 18 19 1a 26 27 28 29 2a 35 36 37 38 39 3a 43 44 45 46 47 48 "
    "49 4a 53 54 55 56 57 58 59 5a 63 64 65 66 67 68 69 6a 73 74 75 76 77 78 79 7a 82 83 84 85 86 87 "
    "88 89 8a 92 93 94 95 96 97 98 99 9a a2 a3 a4 a5 a6 a7 a8 a9 aa b2 b3 b4 b5 b6 b7 b8 b9 ba c2 c3 "
    "c4 c5 c6 c7 c8 c9 ca d2 d3 d4 d5 d6 d7 d8 d9 da e2 e3 e4 e5 e6 e7 e8 e9 ea f2 f3 f4 f5 f6 f7 f8 f9 fa")

# Thứ tự khối trong một MCU (0=Y, 1=Cb, 2=Cr) và các cách bố trí điểm ảnh khả dĩ
SCHEMES = {
    "gray": [0],
    "444": [0, 1, 2],
    "4blk": [0, 0, 1, 2],          # 4:2:2 ngang hoặc dọc
    "6blk": [0, 0, 0, 0, 1, 2],    # 4:2:0 hoặc 4:1:1
}
LAYOUTS = {
    "gray": ["gray"], "444": ["444"],
    "4blk": ["422h", "422v"], "6blk": ["420", "411"],
}
LAYOUT_TO_SCHEME = {l: s for s, ls in LAYOUTS.items() for l in ls}
ROT = {s: [p[i:] + p[:i] for i in range(len(p))] for s, p in SCHEMES.items()}
PAD = b"\xff" * 16
RST_SPLIT = re.compile(b"\xff[\xd0-\xd7]")


# ----------------------------------------------------------------------------
# Bảng Huffman / lượng tử
# ----------------------------------------------------------------------------
class Huff:
    """Bảng tra 16 bit: (độ dài << 8) | ký hiệu, 0 = mã không hợp lệ."""

    def __init__(self, bits, vals):
        lut = [0] * 65536
        code = 0
        k = 0
        for length in range(1, 17):
            for _ in range(bits[length - 1]):
                lo = code << (16 - length)
                hi = (code + 1) << (16 - length)
                if hi > 65536:
                    raise ValueError("bảng Huffman không hợp lệ")
                lut[lo:hi] = [(length << 8) | vals[k]] * (hi - lo)
                code += 1
                k += 1
            code <<= 1
        self.lut = lut


def scale_qt(base, quality):
    quality = max(1, min(100, quality))
    scale = 5000 // quality if quality < 50 else 200 - 2 * quality
    return np.clip((np.array(base, dtype=np.float32) * scale + 50) // 100, 1, 255)


class TableSet:
    def __init__(self, name, dc, ac, qt, schemes=None, layouts=None):
        self.name = name
        self.dc_luts = (dc[0].lut, dc[1].lut)
        self.ac_luts = (ac[0].lut, ac[1].lut)
        self.qt = qt              # (Q luma, Q chroma) theo thứ tự tự nhiên
        self.schemes = schemes    # None = thử tất cả
        self.layouts = layouts    # None = thử tất cả layout của scheme


def standard_tableset(quality):
    dc = (Huff(STD_DC_BITS_L, STD_DC_VALS), Huff(STD_DC_BITS_C, STD_DC_VALS))
    ac = (Huff(STD_AC_BITS_L, STD_AC_VALS_L), Huff(STD_AC_BITS_C, STD_AC_VALS_C))
    qt = (scale_qt(STD_QL, quality), scale_qt(STD_QC, quality))
    return TableSet("chuẩn JPEG", dc, ac, qt)


def load_reference(path, quality):
    """Lấy bảng Huffman, lượng tử và kiểu lấy mẫu từ một JPEG nguyên vẹn cùng nguồn."""
    d = open(path, "rb").read()
    pos = 2
    huff, qt, comps, sos = {}, {}, None, None
    while pos + 4 <= len(d) and d[pos] == 0xFF:
        m = d[pos + 1]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            pos += 2
            continue
        ln = struct.unpack(">H", d[pos + 2:pos + 4])[0]
        seg = d[pos + 4:pos + 2 + ln]
        if m == 0xC4:
            while len(seg) >= 17:
                tc_th = seg[0]
                bits = list(seg[1:17])
                n = sum(bits)
                huff[(tc_th >> 4, tc_th & 15)] = (bits, list(seg[17:17 + n]))
                seg = seg[17 + n:]
        elif m == 0xDB:
            while seg:
                prec, tq = seg[0] >> 4, seg[0] & 15
                n = 64 if prec == 0 else 128
                raw = list(struct.unpack(">64H", seg[1:129]) if prec else seg[1:65])
                nat = [0] * 64
                for k in range(64):
                    nat[ZZ[k]] = raw[k]
                qt[tq] = np.array(nat, dtype=np.float32)
                seg = seg[1 + n:]
        elif m in (0xC0, 0xC1):
            nc = seg[5]
            comps = [(seg[6 + 3 * i], seg[7 + 3 * i] >> 4, seg[7 + 3 * i] & 15, seg[8 + 3 * i])
                     for i in range(nc)]
        elif m == 0xC2:
            raise SystemExit("File tham chiếu là progressive - công cụ này chưa hỗ trợ.")
        elif m == 0xDA:
            ns = seg[0]
            sos = [(seg[1 + 2 * i], seg[2 + 2 * i] >> 4, seg[2 + 2 * i] & 15) for i in range(ns)]
            break
        pos += 2 + ln
    if comps is None or sos is None:
        raise SystemExit("Không đọc được SOF/SOS từ file tham chiếu.")
    # chọn bảng theo SOS: thành phần 0 = Y, thành phần 1 = chroma
    y_dc, y_ac = sos[0][1], sos[0][2]
    c_dc, c_ac = (sos[1][1], sos[1][2]) if len(sos) > 1 else (y_dc, y_ac)
    try:
        dc = (Huff(*huff[(0, y_dc)]), Huff(*huff[(0, c_dc)]))
        ac = (Huff(*huff[(1, y_ac)]), Huff(*huff[(1, c_ac)]))
    except KeyError:
        raise SystemExit("File tham chiếu thiếu bảng Huffman cần thiết.")
    qy = qt.get(comps[0][3], scale_qt(STD_QL, quality))
    qc = qt.get(comps[1][3], scale_qt(STD_QC, quality)) if len(comps) > 1 else qy
    if len(comps) == 1:
        schemes, layouts = ["gray"], ["gray"]
    else:
        hv = (comps[0][1], comps[0][2])
        table = {(1, 1): ("444", "444"), (2, 1): ("4blk", "422h"), (1, 2): ("4blk", "422v"),
                 (2, 2): ("6blk", "420"), (4, 1): ("6blk", "411")}
        if hv not in table:
            raise SystemExit("Kiểu lấy mẫu của file tham chiếu chưa được hỗ trợ: %s" % (hv,))
        schemes, layouts = [table[hv][0]], [table[hv][1]]
    return TableSet("tham chiếu " + os.path.basename(path), dc, ac, (qy, qc), schemes, layouts)


# ----------------------------------------------------------------------------
# Giải mã Huffman baseline từ vị trí bit bất kỳ
# ----------------------------------------------------------------------------
def decode(buf, nbits, pos, pattern, ts, max_blocks, store=True):
    """Giải mã tối đa max_blocks khối 8x8 từ bit `pos`. Dừng ở lỗi đầu tiên.

    Trả về (mảng hệ số 'h', số khối, vị trí bit khối cuối, trạng thái 'err'/'end'/'max').
    Lỗi = mã Huffman không hợp lệ hoặc tràn quá 64 hệ số trong một khối.
    """
    dcl, acl = ts.dc_luts, ts.ac_luts
    out = array("h") if store else None
    preds = [0, 0, 0]
    npat = len(pattern)
    pi = 0
    n = 0
    status = "max"
    while n < max_blocks:
        comp = pattern[pi]
        t = 1 if comp else 0
        dlut = dcl[t]
        alut = acl[t]
        p = pos
        i = p >> 3
        e = dlut[(((buf[i] << 24) | (buf[i + 1] << 16) | (buf[i + 2] << 8) | buf[i + 3])
                  >> (16 - (p & 7))) & 0xFFFF]
        if not e:
            status = "err"
            break
        p += e >> 8
        s = e & 255
        if s:
            if s > 11:
                status = "err"
                break
            i = p >> 3
            v = (buf[i] << 24) | (buf[i + 1] << 16) | (buf[i + 2] << 8) | buf[i + 3]
            bits = (v >> (32 - s - (p & 7))) & ((1 << s) - 1)
            p += s
            diff = bits if bits >= (1 << (s - 1)) else bits - (1 << s) + 1
        else:
            diff = 0
        dc = preds[comp] + diff
        blk = [0] * 64
        blk[0] = dc
        k = 1
        bad = False
        while k < 64:
            i = p >> 3
            e = alut[(((buf[i] << 24) | (buf[i + 1] << 16) | (buf[i + 2] << 8) | buf[i + 3])
                      >> (16 - (p & 7))) & 0xFFFF]
            if not e:
                bad = True
                break
            p += e >> 8
            rs = e & 255
            s = rs & 15
            if s:
                if s > 10:
                    bad = True
                    break
                k += rs >> 4
                if k > 63:
                    bad = True
                    break
                i = p >> 3
                v = (buf[i] << 24) | (buf[i + 1] << 16) | (buf[i + 2] << 8) | buf[i + 3]
                bits = (v >> (32 - s - (p & 7))) & ((1 << s) - 1)
                p += s
                blk[k] = bits if bits >= (1 << (s - 1)) else bits - (1 << s) + 1
                k += 1
            elif rs == 0xF0:
                k += 16
                if k > 64:
                    bad = True
                    break
            else:
                break  # EOB
        if bad:
            status = "err"
            break
        if p > nbits:
            status = "end"
            break
        preds[comp] = dc
        if store:
            out.extend(blk)
        n += 1
        pos = p
        pi += 1
        if pi == npat:
            pi = 0
    return out, n, pos, status


def sync_search(buf, nbits, combos, start_from, span, probe, cap=300, fixed_phase=None):
    """Tìm vị trí bit + (bảng, kiểu lấy mẫu, pha) cho phép giải mã liền >= probe khối không lỗi."""
    end = min(start_from + span, nbits - 8 * 24)
    for start in range(start_from, end):
        cands = []
        for ts, sch in combos:
            phases = [fixed_phase] if fixed_phase is not None else range(len(SCHEMES[sch]))
            for ph in phases:
                _, n, _, _ = decode(buf, nbits, start, ROT[sch][ph], ts, probe, False)
                if n >= probe:
                    cands.append((ts, sch, ph))
        if cands:
            best = None
            for ts, sch, ph in cands:
                _, n, _, _ = decode(buf, nbits, start, ROT[sch][ph], ts, cap, False)
                if best is None or n > best[0]:
                    best = (n, ts, sch, ph)
            return best[1], best[2], best[3], start
    return None


def make_combos(tablesets, ns_hint):
    combos = []
    for ts in tablesets:
        for sch in SCHEMES:
            if ns_hint == 1 and sch != "gray":
                continue
            if ns_hint == 3 and sch == "gray":
                continue
            if ts.schemes and sch not in ts.schemes:
                continue
            combos.append((ts, sch))
    return combos


def process_fragment(data, known_start, ns_hint, tablesets, args):
    """Trả về (danh sách mảnh giải mã được, ghi chú). Mỗi mảnh: dict coefs/scheme/phase/ts."""
    segs = RST_SPLIT.split(data)
    pieces = []
    notes = []
    max_blocks = args.max_mb * 1024 * 1024 // 8
    combos = make_combos(tablesets, ns_hint)

    def prep(seg):
        b = seg.replace(b"\xff\x00", b"\xff")
        return b + PAD, len(b) * 8

    def as_array(arr):
        return np.frombuffer(arr, dtype=np.int16).reshape(-1, 64)

    if len(segs) > 1:
        # Có restart marker: các đoạn sau RST bắt đầu đúng ranh giới MCU, DC reset về 0
        notes.append("có RST (%d đoạn), bỏ đoạn đầu" % len(segs))
        usable = [s for s in segs[1:] if len(s) >= 16]
        if not usable:
            return [], notes + ["đoạn sau RST quá ngắn"]
        found = None
        for seg in usable[:4]:
            buf, nbits = prep(seg)
            found = sync_search(buf, nbits, combos, 0, 1, args.probe, fixed_phase=0)
            if found:
                break
        if not found:
            return [], notes + ["không khớp bảng Huffman nào"]
        ts, sch, _, _ = found
        arrays = []
        for seg in usable:
            buf, nbits = prep(seg)
            arr, n, _, _ = decode(buf, nbits, 0, ROT[sch][0], ts, max_blocks)
            if n:
                arrays.append(as_array(arr))
        if arrays:
            pieces.append({"coefs": np.concatenate(arrays), "scheme": sch, "phase": 0, "ts": ts})
        return pieces, notes

    buf, nbits = prep(segs[0])
    pos = 0
    used = combos
    span = 1 if known_start else args.sync_span
    while pos < nbits - 8 * 24:
        found = sync_search(buf, nbits, used, pos, span, args.probe, fixed_phase=0 if known_start else None)
        if not found:
            if not pieces:
                notes.append("không đồng bộ được (sai bảng Huffman / không phải baseline?)")
            break
        ts, sch, ph, start = found
        arr, n, endpos, status = decode(buf, nbits, start, ROT[sch][ph], ts, max_blocks)
        if n >= args.probe:
            pieces.append({"coefs": as_array(arr), "scheme": sch, "phase": ph, "ts": ts,
                           "start_bit": start})
        used = [(ts, sch)]
        known_start = False
        span = args.sync_span
        pos = max(endpos, start) + 1
    return pieces, notes


# ----------------------------------------------------------------------------
# Dựng ảnh từ hệ số DCT
# ----------------------------------------------------------------------------
_A = np.zeros((8, 8), dtype=np.float32)
for _u in range(8):
    for _x in range(8):
        _A[_u, _x] = (np.sqrt(0.5) if _u == 0 else 1.0) * np.cos((2 * _x + 1) * _u * np.pi / 16) / 2


def luma_patches(Y, layout):
    if layout in ("gray", "444"):
        return Y[:, 0]
    if layout == "422h":
        return np.concatenate([Y[:, 0], Y[:, 1]], axis=2)
    if layout == "422v":
        return np.concatenate([Y[:, 0], Y[:, 1]], axis=1)
    if layout == "420":
        top = np.concatenate([Y[:, 0], Y[:, 1]], axis=2)
        bot = np.concatenate([Y[:, 2], Y[:, 3]], axis=2)
        return np.concatenate([top, bot], axis=1)
    if layout == "411":
        return np.concatenate([Y[:, 0], Y[:, 1], Y[:, 2], Y[:, 3]], axis=2)
    raise ValueError(layout)


def upsample_chroma(C, layout):
    if layout in ("gray", "444"):
        return C
    if layout == "422h":
        return np.repeat(C, 2, axis=2)
    if layout == "422v":
        return np.repeat(C, 2, axis=1)
    if layout == "420":
        return np.repeat(np.repeat(C, 2, axis=1), 2, axis=2)
    if layout == "411":
        return np.repeat(C, 4, axis=2)
    raise ValueError(layout)


def detect_width(P, wmin=4):
    """Độ rộng (tính theo MCU) làm hàng khối liền kề liên tục nhất theo chiều dọc."""
    n = len(P)
    wmax = min(n // 2, 4096)
    if wmax < wmin:
        return None, None
    top, bot = P[:, 0, :], P[:, -1, :]
    costs = np.array([np.abs(bot[:n - w] - top[w:]).mean() for w in range(wmin, wmax + 1)])
    k = int(np.argmin(costs))
    conf = float(costs[k] / (np.median(costs) + 1e-6))
    return wmin + k, conf


def render_piece(piece, args):
    scheme, ts = piece["scheme"], piece["ts"]
    pattern = SCHEMES[scheme]
    nblk = len(pattern)
    lead = (nblk - piece["phase"]) % nblk
    c = piece["coefs"][lead:]
    n_mcu = len(c) // nblk
    if n_mcu < args.min_mcus:
        return None, "quá ngắn (%d MCU)" % n_mcu
    c = c[:n_mcu * nblk].reshape(n_mcu, nblk, 64)
    nat = np.zeros((n_mcu, nblk, 64), dtype=np.float32)
    nat[:, :, ZZ] = c
    q = np.stack([ts.qt[0] if comp == 0 else ts.qt[1] for comp in pattern])
    nat *= q[None]
    sp = (_A.T @ nat.reshape(-1, 8, 8) @ _A).reshape(n_mcu, nblk, 8, 8)
    ny = pattern.count(0)
    Y = sp[:, :ny]
    Cb = sp[:, ny] if nblk > ny else None
    Cr = sp[:, ny + 1] if nblk > ny else None

    layouts = ts.layouts if ts.layouts and LAYOUT_TO_SCHEME.get(ts.layouts[0]) == scheme else LAYOUTS[scheme]
    best = None
    for lay in layouts:
        P = luma_patches(Y, lay)
        if args.width:
            w, conf = args.width, 0.0
        else:
            w, conf = detect_width(P)
        if w is None:
            continue
        if best is None or conf < best[2]:
            best = (lay, w, conf, P)
    if best is None:
        return None, "không dò được độ rộng"
    lay, W, conf, P = best

    # Dịch vòng ngang: vị trí đầu hàng thật là nơi hai MCU kề nhau lệch nhau nhiều nhất
    n = len(P)
    hd = np.abs(P[:-1, :, -1] - P[1:, :, 0]).mean(axis=1)
    res = (np.arange(1, n)) % W
    sums = np.bincount(res, weights=hd, minlength=W)
    cnts = np.bincount(res, minlength=W)
    start = int(np.argmax(sums / np.maximum(cnts, 1)))
    rows = (n - start) // W
    if rows < 2:
        return None, "không đủ hàng"
    sel = slice(start, start + rows * W)

    def assemble(X):
        h, w = X.shape[1], X.shape[2]
        return X[sel].reshape(rows, W, h, w).transpose(0, 2, 1, 3).reshape(rows * h, W * w)

    Yimg = assemble(P)
    lo, hi = np.percentile(Yimg, [0.5, 99.5])
    scale = 255.0 / max(hi - lo, 1e-3)
    if Cb is None:
        arr = np.clip((Yimg - lo) * scale, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr, "L")
    else:
        cb = assemble(upsample_chroma(Cb, lay))
        cr = assemble(upsample_chroma(Cr, lay))
        cb = (cb - cb.mean()) * 1.0
        cr = (cr - cr.mean()) * 1.0
        Yn = (Yimg - lo) * scale
        r = Yn + 1.402 * cr
        g = Yn - 0.344136 * cb - 0.714136 * cr
        b = Yn + 1.772 * cb
        arr = np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)
        img = Image.fromarray(arr, "RGB")
    info = "%s, rộng %d MCU (%dpx), %d hàng, độ tin cậy độ rộng %.2f" % (
        lay, W, img.width, rows, conf)
    return img, info


# ----------------------------------------------------------------------------
# Quét ảnh đĩa
# ----------------------------------------------------------------------------
def chunk_check(arr, need_ff00):
    """Khối hợp lệ nếu sau mọi byte FF chỉ là 00, RSTn hoặc EOI. Trả về (ok, vị trí EOI)."""
    if len(arr) < 2:
        return False, None
    ff = np.flatnonzero(arr[:-1] == 0xFF)
    nxt = arr[ff + 1]
    eoi_idx = np.flatnonzero(nxt == 0xD9)
    eoi = None
    if eoi_idx.size:
        eoi = int(ff[eoi_idx[0]])
        keep = ff < eoi
        nxt = nxt[keep]
    bad = (nxt != 0) & ~((nxt >= 0xD0) & (nxt <= 0xD7))
    ok = (not bad.any()) and int((nxt == 0).sum()) >= need_ff00
    return ok, eoi


def extend(mm, size, start, block, need_first):
    """Mở rộng mảnh từ `start` qua các khối liên tiếp còn hợp lệ. Trả về vị trí kết thúc."""
    cur = start
    stop = min(((cur // block) + 1) * block, size)
    first = True
    while cur < size:
        arr = np.frombuffer(mm[cur:stop], dtype=np.uint8)
        ok, eoi = chunk_check(arr, need_first if first else 0)
        if eoi is not None:
            return cur + eoi if (ok or not first) else None
        if not ok:
            break
        if not first and len(np.unique(arr)) < 64:
            break
        first = False
        cur = stop
        stop = min(cur + block, size)
    return cur if not first else None


def find_sos(mm, size, block, args):
    """Tìm FFDA + header quét hợp lệ (baseline). Trả về danh sách (offset_dữ_liệu, Ns)."""
    hits = []
    pos = 0
    while True:
        p = mm.find(b"\xff\xda", pos)
        if p < 0:
            break
        pos = p + 2
        if p + 12 > size:
            continue
        ln = (mm[p + 2] << 8) | mm[p + 3]
        ns = mm[p + 4]
        if ns not in (1, 3) or ln != 6 + 2 * ns:
            continue
        e = p + 2 + ln
        if e >= size or mm[e - 3] != 0 or mm[e - 2] != 63 or mm[e - 1] != 0:
            continue
        ok = True
        for i in range(ns):
            tb = mm[p + 6 + 2 * i]
            if (tb >> 4) > 1 or (tb & 15) > 1:
                ok = False
        if ok:
            hits.append((e, ns))
    return hits


def main():
    ap = argparse.ArgumentParser(description="Khôi phục mảnh JPEG mồ côi (baseline) từ ảnh đĩa hoặc tệp nhị phân.")
    ap.add_argument("input", help="ảnh đĩa / tệp dữ liệu")
    ap.add_argument("-o", "--out", default="carved", help="thư mục xuất (mặc định: carved)")
    ap.add_argument("--raw", action="store_true", help="coi cả tệp là một mảnh dữ liệu entropy (không quét)")
    ap.add_argument("--block-size", type=int, default=4096, help="kích thước cụm/khối căn chỉnh (mặc định 4096)")
    ap.add_argument("--min-size", type=int, default=8192, help="bỏ mảnh nhỏ hơn số byte này")
    ap.add_argument("--min-ff00", type=int, default=2, help="số FF00 tối thiểu trong khối mở đầu mảnh")
    ap.add_argument("--ref", action="append", default=[], help="JPEG nguyên vẹn cùng nguồn để lấy bảng Huffman/lượng tử (dùng được nhiều lần)")
    ap.add_argument("--quality", type=int, default=85, help="chất lượng giả định cho bảng lượng tử chuẩn")
    ap.add_argument("--width", type=int, help="ép độ rộng ảnh (tính bằng MCU) nếu đã biết")
    ap.add_argument("--probe", type=int, default=32, help="số khối liên tiếp không lỗi để coi là đã đồng bộ")
    ap.add_argument("--sync-span", type=int, default=6000, help="số vị trí bit thử khi tìm đồng bộ")
    ap.add_argument("--min-mcus", type=int, default=16, help="số MCU tối thiểu để dựng ảnh")
    ap.add_argument("--max-mb", type=int, default=4, help="giải mã tối đa MB dữ liệu cho mỗi mảnh")
    ap.add_argument("--dump-bin", action="store_true", help="lưu thêm dữ liệu entropy thô của mỗi mảnh (.bin)")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.makedirs(args.out, exist_ok=True)

    tablesets = [load_reference(r, args.quality) for r in args.ref]
    tablesets.append(standard_tableset(args.quality))

    f = open(args.input, "rb")
    size = os.fstat(f.fileno()).st_size
    if size == 0:
        raise SystemExit("Tệp rỗng.")
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

    # ---- 1) lập danh sách mảnh ứng viên
    frags = []  # (start, end, known_start, ns, nguồn)
    if args.raw:
        frags.append((0, size, False, None, "raw"))
    else:
        covered_to = -1
        for e, ns in find_sos(mm, size, args.block_size, args):
            end = extend(mm, size, e, args.block_size, 0)
            if end and end - e >= args.min_size:
                frags.append((e, end, True, ns, "SOS"))
        covered = [(s, t) for s, t, *_ in frags]
        nblocks = (size + args.block_size - 1) // args.block_size
        i = 0
        while i < nblocks:
            off = i * args.block_size
            if any(s <= off < t for s, t in covered):
                i += 1
                continue
            arr = np.frombuffer(mm[off:off + args.block_size], dtype=np.uint8)
            ok, _ = chunk_check(arr, args.min_ff00)
            if ok:
                end = extend(mm, size, off, args.block_size, args.min_ff00)
                if end and end - off >= args.min_size:
                    frags.append((off, end, False, None, "mồ côi"))
                    i = (end + args.block_size - 1) // args.block_size
                    continue
            i += 1
    frags.sort()
    print("Tìm thấy %d mảnh ứng viên (>= %d byte)." % (len(frags), args.min_size))

    # ---- 2) giải mã từng mảnh
    report = []
    for idx, (s, t, known, ns, src) in enumerate(frags):
        data = bytes(mm[s:t])
        print("\n[%d] offset 0x%x, %d byte, nguồn: %s" % (idx, s, t - s, src))
        if args.dump_bin:
            with open(os.path.join(args.out, "frag_%010x.bin" % s), "wb") as fo:
                fo.write(data)
        pieces, notes = process_fragment(data, known, ns, tablesets, args)
        for nt in notes:
            print("    - " + nt)
        if not pieces:
            print("    -> không khôi phục được")
            continue
        for j, pc in enumerate(pieces):
            img, info = render_piece(pc, args)
            nb = len(pc["coefs"])
            if img is None:
                print("    mảnh %d: %d khối, bỏ qua (%s)" % (j, nb, info))
                continue
            name = "frag_%010x_%d.png" % (s, j)
            img.save(os.path.join(args.out, name))
            print("    mảnh %d: %s, %s, bảng '%s' -> %s (%dx%d)" % (
                j, pc["scheme"], info, pc["ts"].name, name, img.width, img.height))
            report.append((name, s, t - s, pc["scheme"], pc["ts"].name, img.width, img.height))

    with open(os.path.join(args.out, "report.csv"), "w", encoding="utf-8") as fr:
        fr.write("file,offset,size,scheme,tables,width,height\n")
        for r in report:
            fr.write("%s,0x%x,%d,%s,%s,%d,%d\n" % r)
    print("\nXong: %d ảnh được lưu trong '%s'." % (len(report), args.out))


if __name__ == "__main__":
    main()
