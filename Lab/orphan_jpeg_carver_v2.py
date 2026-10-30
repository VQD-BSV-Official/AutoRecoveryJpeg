#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orphan_jpeg_carver.py - Tìm và khôi phục mảnh JPEG "mồ côi" (không còn header).
BẢN TỐI ƯU TỐC ĐỘ + xuất JPEG (thay vì PNG).

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
     kéo giãn độ sáng/màu (vì DC chỉ còn là giá trị tương đối) rồi lưu JPEG.

Các điểm tăng tốc so với bản cũ:
  - Giải mã Huffman + dò đồng bộ chạy bằng numba (nếu đã cài `pip install numba`),
    tự động quay về Python thuần nếu không có / biên dịch lỗi.
  - Quét ứng viên trên toàn ảnh đĩa bằng numpy theo lô lớn (không lặp từng khối bằng Python).
  - Xử lý song song nhiều mảnh bằng nhiều tiến trình (--jobs).
  - IDCT gộp zigzag + lượng tử thành 1 phép nhân ma trận 64x64.
  - Dò độ rộng chỉ so khớp trên một cửa sổ mẫu, không quét toàn bộ.
  - Ghép màu YCbCr trực tiếp vào JPEG, không đổi sang RGB bằng float.

Cách dùng:
  python orphan_jpeg_carver.py disk.img -o out
  python orphan_jpeg_carver.py raw_fragment.bin --raw -o out
  python orphan_jpeg_carver.py disk.img --ref anh_tot_cung_may.jpg -o out
  python orphan_jpeg_carver.py disk.img -o out --jobs 8 --jpeg-quality 85
"""
import os

# Mỗi tiến trình chỉ dùng 1 luồng BLAS để các tiến trình song song không giẫm chân nhau.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import multiprocessing
import re
import struct
import sys
import mmap
import time
from array import array
from bisect import bisect_right

import numpy as np
from PIL import Image

try:  # numba là tuỳ chọn
    from numba import njit
    HAVE_NUMBA = True
except Exception:  # pragma: no cover
    HAVE_NUMBA = False

    def njit(*a, **k):
        if len(a) == 1 and callable(a[0]) and not k:
            return a[0]
        return lambda f: f

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
        self.idx = 0              # chỉ số trong bảng LUT numba


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
# Nhân numba (cũng là Python hợp lệ nếu không có numba)
# ----------------------------------------------------------------------------
SCH_NAMES = list(SCHEMES)                       # gray, 444, 4blk, 6blk
SCH_ID = {n: i for i, n in enumerate(SCH_NAMES)}
PLEN = np.array([len(SCHEMES[n]) for n in SCH_NAMES], dtype=np.int64)
ROT_ARR = np.zeros((len(SCH_NAMES), 6, 6), dtype=np.int64)
for _i, _n in enumerate(SCH_NAMES):
    for _ph, _p in enumerate(ROT[_n]):
        ROT_ARR[_i, _ph, :len(_p)] = _p


@njit(cache=True, nogil=True)
def _win32(buf, p, nbytes):
    """32 bit bắt đầu từ byte chứa bit p (byte vượt biên coi là 0xFF)."""
    i = p >> 3
    if i + 3 < nbytes:
        return ((np.int64(buf[i]) << 24) | (np.int64(buf[i + 1]) << 16)
                | (np.int64(buf[i + 2]) << 8) | np.int64(buf[i + 3]))
    v = np.int64(0)
    for j in range(4):
        v = v << 8
        if i + j < nbytes:
            v = v | np.int64(buf[i + j])
        else:
            v = v | 255
    return v


@njit(cache=True, nogil=True)
def _decode_k(buf, nbits, pos, pat, plen, dc0, dc1, ac0, ac1, max_blocks, store, out):
    """Giải mã tối đa max_blocks khối từ bit `pos`. status: 0=max, 1=lỗi, 2=hết dữ liệu."""
    nbytes = buf.shape[0]
    cap = out.shape[0] // 64
    n = 0
    pi = 0
    status = 0
    pr0 = 0
    pr1 = 0
    pr2 = 0
    while n < max_blocks:
        if store and n >= cap:
            newcap = cap * 2 if cap > 0 else 1024
            if newcap > max_blocks:
                newcap = max_blocks
            new = np.zeros(newcap * 64, np.int16)
            new[:n * 64] = out[:n * 64]
            out = new
            cap = newcap
        base = n * 64
        comp = pat[pi]
        if comp == 0:
            dlut = dc0
            alut = ac0
        else:
            dlut = dc1
            alut = ac1
        p = pos
        e = dlut[(_win32(buf, p, nbytes) >> (16 - (p & 7))) & 0xFFFF]
        if e == 0:
            status = 1
            break
        p += e >> 8
        s = e & 255
        diff = 0
        if s:
            if s > 11:
                status = 1
                break
            v = _win32(buf, p, nbytes)
            bits = (v >> (32 - s - (p & 7))) & ((1 << s) - 1)
            p += s
            if bits >= (1 << (s - 1)):
                diff = bits
            else:
                diff = bits - (1 << s) + 1
        if comp == 0:
            dc = pr0 + diff
        elif comp == 1:
            dc = pr1 + diff
        else:
            dc = pr2 + diff
        if store:
            out[base] = min(max(dc, -32768), 32767)
        k = 1
        bad = False
        while k < 64:
            e = alut[(_win32(buf, p, nbytes) >> (16 - (p & 7))) & 0xFFFF]
            if e == 0:
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
                v = _win32(buf, p, nbytes)
                bits = (v >> (32 - s - (p & 7))) & ((1 << s) - 1)
                p += s
                if store:
                    if bits >= (1 << (s - 1)):
                        out[base + k] = bits
                    else:
                        out[base + k] = bits - (1 << s) + 1
                k += 1
            elif rs == 0xF0:
                k += 16
                if k > 64:
                    bad = True
                    break
            else:
                break
        if bad:
            status = 1
            break
        if p > nbits:
            status = 2
            break
        if comp == 0:
            pr0 = dc
        elif comp == 1:
            pr1 = dc
        else:
            pr2 = dc
        n += 1
        pos = p
        pi += 1
        if pi == plen:
            pi = 0
    return out, n, pos, status


@njit(cache=True, nogil=True)
def _sync_k(buf, nbits, luts, c_ts, c_sch, rot, plen, start_from, span, probe, cap, fixed_phase):
    """Toàn bộ vòng dò đồng bộ trong mã máy. Trả (chỉ số tổ hợp, pha, bit) hoặc (-1,-1,-1)."""
    dummy = np.empty(0, np.int16)
    end = min(start_from + span, nbits - 8 * 24)
    for start in range(start_from, end):
        best_n = -1
        best_c = -1
        best_ph = -1
        for c in range(c_ts.shape[0]):
            t = c_ts[c]
            sch = c_sch[c]
            L = plen[sch]
            if fixed_phase >= 0:
                lo = fixed_phase
                hi = fixed_phase + 1
            else:
                lo = 0
                hi = L
            for ph in range(lo, hi):
                r = _decode_k(buf, nbits, start, rot[sch, ph], L,
                              luts[t, 0], luts[t, 1], luts[t, 2], luts[t, 3],
                              probe, False, dummy)
                if r[1] >= probe:
                    r2 = _decode_k(buf, nbits, start, rot[sch, ph], L,
                                   luts[t, 0], luts[t, 1], luts[t, 2], luts[t, 3],
                                   cap, False, dummy)
                    if r2[1] > best_n:
                        best_n = r2[1]
                        best_c = c
                        best_ph = ph
        if best_c >= 0:
            return best_c, best_ph, start
    return -1, -1, -1


# Trạng thái JIT của tiến trình hiện tại
_JIT = False
_LUTS = None


def _setup_jit(tablesets):
    """Dựng bảng LUT dạng numpy và biên dịch thử. Thất bại -> trả False (dùng Python thuần)."""
    global _JIT, _LUTS
    _JIT = False
    try:
        stacks = []
        for i, ts in enumerate(tablesets):
            ts.idx = i
            stacks.append(np.stack([np.array(l, dtype=np.int32)
                                    for l in (ts.dc_luts[0], ts.dc_luts[1],
                                              ts.ac_luts[0], ts.ac_luts[1])]))
        _LUTS = np.ascontiguousarray(np.stack(stacks))
        buf = np.frombuffer(bytearray(os.urandom(512) + PAD), dtype=np.uint8)
        _sync_k(buf, 512 * 8, _LUTS, np.array([0], np.int64), np.array([1], np.int64),
                ROT_ARR, PLEN, 0, 8, 4, 8, -1)
        out = np.zeros(64, np.int16)
        _decode_k(buf, 512 * 8, 0, ROT_ARR[1, 0], 3, _LUTS[0, 0], _LUTS[0, 1],
                  _LUTS[0, 2], _LUTS[0, 3], 4, True, out)
        _JIT = True
    except Exception as ex:  # lỗi biên dịch -> dùng đường Python thuần
        print("Cảnh báo: JIT numba lỗi (%s: %s), dùng Python thuần." % (type(ex).__name__, ex))
    return _JIT


# ----------------------------------------------------------------------------
# Giải mã Huffman baseline từ vị trí bit bất kỳ
# ----------------------------------------------------------------------------
def prep_buf(seg):
    """Bỏ byte stuffing, thêm PAD. Trả (bộ đệm dùng cho decode, số bit dữ liệu thật)."""
    b = seg.replace(b"\xff\x00", b"\xff")
    nbits = len(b) * 8
    b = b + PAD
    if _JIT:
        return np.frombuffer(bytearray(b), dtype=np.uint8), nbits
    # Python thuần: dựng sẵn cửa sổ 32 bit tại mỗi byte để mỗi lần tra chỉ cần 1 phép index
    a = np.frombuffer(b, dtype=np.uint8)
    w = ((a[:-3].astype(np.uint32) << 24) | (a[1:-2].astype(np.uint32) << 16)
         | (a[2:-1].astype(np.uint32) << 8) | a[3:].astype(np.uint32))
    win = array("I" if array("I").itemsize == 4 else "L")
    win.frombytes(w.tobytes())
    return win, nbits


_STATUS = ("max", "err", "end")


def decode(win, nbits, pos, pattern, ts, max_blocks, store=True):
    """Giải mã tối đa max_blocks khối 8x8 từ bit `pos`. Dừng ở lỗi đầu tiên.

    Trả về (mảng hệ số, số khối, vị trí bit khối cuối, trạng thái 'err'/'end'/'max').
    Lỗi = mã Huffman không hợp lệ hoặc tràn quá 64 hệ số trong một khối.
    """
    if _JIT:
        pat = np.zeros(6, dtype=np.int64)
        pat[:len(pattern)] = pattern
        out = np.zeros((min(max_blocks, 8192) if store else 0) * 64, dtype=np.int16)
        lt = _LUTS[ts.idx]
        out, n, pos, st = _decode_k(win, nbits, pos, pat, len(pattern), lt[0], lt[1], lt[2], lt[3],
                                    max_blocks, store, out)
        return (out[:n * 64] if store else None), n, pos, _STATUS[st]

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
        e = dlut[(win[p >> 3] >> (16 - (p & 7))) & 0xFFFF]
        if not e:
            status = "err"
            break
        p += e >> 8
        s = e & 255
        if s:
            if s > 11:
                status = "err"
                break
            bits = (win[p >> 3] >> (32 - s - (p & 7))) & ((1 << s) - 1)
            p += s
            diff = bits if bits >= (1 << (s - 1)) else bits - (1 << s) + 1
        else:
            diff = 0
        dc = preds[comp] + diff
        if store:
            blk = [0] * 64
            blk[0] = max(-32768, min(32767, dc))
        k = 1
        bad = False
        while k < 64:
            e = alut[(win[p >> 3] >> (16 - (p & 7))) & 0xFFFF]
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
                bits = (win[p >> 3] >> (32 - s - (p & 7))) & ((1 << s) - 1)
                p += s
                if store:
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
    if _JIT:
        c_ts = np.array([ts.idx for ts, _ in combos], dtype=np.int64)
        c_sch = np.array([SCH_ID[s] for _, s in combos], dtype=np.int64)
        ci, ph, start = _sync_k(buf, nbits, _LUTS, c_ts, c_sch, ROT_ARR, PLEN, start_from, span,
                                probe, cap, -1 if fixed_phase is None else fixed_phase)
        if ci < 0:
            return None
        ts, sch = combos[ci]
        return ts, sch, int(ph), int(start)

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
            buf, nbits = prep_buf(seg)
            found = sync_search(buf, nbits, combos, 0, 1, args.probe, fixed_phase=0)
            if found:
                break
        if not found:
            return [], notes + ["không khớp bảng Huffman nào"]
        ts, sch, _, _ = found
        arrays = []
        for seg in usable:
            buf, nbits = prep_buf(seg)
            arr, n, _, _ = decode(buf, nbits, 0, ROT[sch][0], ts, max_blocks)
            if n:
                arrays.append(as_array(arr))
        if arrays:
            pieces.append({"coefs": np.concatenate(arrays), "scheme": sch, "phase": 0, "ts": ts})
        return pieces, notes

    buf, nbits = prep_buf(segs[0])
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
_K = np.kron(_A, _A).astype(np.float32)          # IDCT 2D = 1 phép nhân với ma trận 64x64
_ZZ = np.array(ZZ)
CHROMA_REP = {"gray": (1, 1), "444": (1, 1), "422h": (1, 2), "422v": (2, 1), "420": (2, 2), "411": (1, 4)}
MAX_JPEG_DIM = 65000


def idct_mats(ts):
    """Ma trận (zigzag -> điểm ảnh) đã nhân sẵn bảng lượng tử, cho luma và chroma."""
    m = getattr(ts, "_idct", None)
    if m is None:
        m = tuple((_K[_ZZ] * np.asarray(ts.qt[i], dtype=np.float32)[_ZZ][:, None]).astype(np.float32)
                  for i in (0, 1))
        ts._idct = m
    return m


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


def detect_width(P, wmin=4, window=4096):
    """Độ rộng (tính theo MCU) làm hàng khối liền kề liên tục nhất theo chiều dọc.

    Chỉ so khớp trên một cửa sổ liên tiếp (tối đa `window` khối) ở giữa dữ liệu thay vì
    toàn bộ: chi phí giảm từ O(n * wmax) xuống O(window * wmax), kết quả gần như không đổi.
    """
    n = len(P)
    wmax = min(n // 2, 4096)
    if wmax < wmin:
        return None, None
    top, bot = P[:, 0, :], P[:, -1, :]
    S = min(n - wmax, window)
    a = (n - wmax - S) // 2
    B = np.ascontiguousarray(bot[a:a + S], dtype=np.float32)
    T = np.ascontiguousarray(top[a:a + S + wmax], dtype=np.float32)
    tmp = np.empty_like(B)
    costs = np.empty(wmax - wmin + 1)
    for k, w in enumerate(range(wmin, wmax + 1)):
        np.subtract(B, T[w:w + S], out=tmp)
        np.abs(tmp, out=tmp)
        costs[k] = tmp.sum() / tmp.size
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
    ny = pattern.count(0)
    MY, MC = idct_mats(ts)
    Y = (c[:, :ny].reshape(-1, 64).astype(np.float32) @ MY).reshape(n_mcu, ny, 8, 8)
    if nblk > ny:
        Cb = (c[:, ny].astype(np.float32) @ MC).reshape(n_mcu, 8, 8)
        Cr = (c[:, ny + 1].astype(np.float32) @ MC).reshape(n_mcu, 8, 8)
    else:
        Cb = Cr = None

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
    sub = Yimg[::2, ::2] if Yimg.size > 4_000_000 else Yimg
    lo, hi = np.percentile(sub, [0.5, 99.5])
    scale = 255.0 / max(hi - lo, 1e-3)
    Yimg -= lo
    Yimg *= scale                                   # chưa cắt: bước đổi RGB cần giá trị gốc như bản cũ
    if Cb is None:
        img = Image.fromarray(np.clip(Yimg, 0, 255).astype(np.uint8))
    else:
        fy, fx = CHROMA_REP[lay]
        cs = []
        for C in (Cb, Cr):
            cc = assemble(C)
            cc -= cc.mean()                         # chroma căn quanh giá trị trung bình
            cs.append(cc)
        if max(float(np.abs(cs[0]).max()), float(np.abs(cs[1]).max())) <= 127.5:
            # Chroma nằm gọn trong biên độ YCbCr: ghép thẳng YCbCr vào JPEG, bỏ qua đổi RGB bằng float
            planes = [Image.fromarray(np.clip(Yimg, 0, 255).astype(np.uint8))]
            for cc in cs:
                c8 = np.clip(np.rint(cc + 128.0), 0, 255).astype(np.uint8)
                if fy > 1:
                    c8 = np.repeat(c8, fy, axis=0)
                if fx > 1:
                    c8 = np.repeat(c8, fx, axis=1)
                planes.append(Image.fromarray(c8))
            img = Image.merge("YCbCr", tuple(planes))
        else:
            # Chroma vượt biên độ (DC lệch tương đối): đổi RGB bằng float, đúng như bản gốc
            cb, cr = cs
            if fy > 1:
                cb, cr = np.repeat(cb, fy, axis=0), np.repeat(cr, fy, axis=0)
            if fx > 1:
                cb, cr = np.repeat(cb, fx, axis=1), np.repeat(cr, fx, axis=1)
            r = Yimg + 1.402 * cr
            g = Yimg - 0.344136 * cb - 0.714136 * cr
            b = Yimg + 1.772 * cb
            img = Image.fromarray(np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8))
    info = "%s, rộng %d MCU (%dpx), %d hàng, độ tin cậy độ rộng %.2f" % (
        lay, W, img.width, rows, conf)
    return img, info


def save_jpeg(img, outdir, base, quality, subsampling=2):
    """Lưu JPEG. Ảnh cao hơn giới hạn JPEG (65500px) được cắt thành nhiều tệp _p1, _p2..."""
    h = img.height
    spans = [(0, h)] if h <= MAX_JPEG_DIM else [(y, min(y + MAX_JPEG_DIM, h)) for y in range(0, h, MAX_JPEG_DIM)]
    saved = []
    for k, (y0, y1) in enumerate(spans):
        part = img if len(spans) == 1 else img.crop((0, y0, img.width, y1))
        name = base + (".jpg" if len(spans) == 1 else "_p%d.jpg" % (k + 1))
        part.save(os.path.join(outdir, name), "JPEG", quality=quality, subsampling=subsampling)
        saved.append((name, part.width, part.height))
    return saved


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


_CLS = np.zeros(256, dtype=np.uint8)     # 0 = lạ, 1 = 00, 2 = RSTn, 3 = EOI
_CLS[0] = 1
_CLS[0xD0:0xD8] = 2
_CLS[0xD9] = 3


def candidate_blocks(mm, size, block, min_ff00, lot=4 << 20):
    """Lọc nhanh bằng numpy theo lô lớn: chỉ số các khối CÓ THỂ là đầu mảnh.

    Là tập bao (superset) của các khối qua được chunk_check; khối thật sự vẫn được
    kiểm lại chính xác bằng chunk_check ở bước sau.
    """
    step = max(block, (lot // block) * block)
    found = []
    for base in range(0, size, step):
        n = min(step, size - base)
        a = np.frombuffer(mm, dtype=np.uint8, count=n, offset=base)
        ff = np.flatnonzero(a[:-1] == 0xFF)
        if ff.size == 0:
            continue
        ff = ff[(ff % block) != block - 1]          # FF ở byte cuối khối: bỏ qua như chunk_check
        if ff.size == 0:
            continue
        cls = _CLS[a[ff + 1]]
        blk = ff // block
        nb = (n + block - 1) // block
        bad = np.bincount(blk[cls == 0], minlength=nb)
        z = np.bincount(blk[cls == 1], minlength=nb)
        e = np.bincount(blk[cls == 3], minlength=nb)
        cand = ((bad == 0) | (e > 0)) & (z >= min_ff00)
        idx = np.flatnonzero(cand)
        if idx.size:
            found.append(idx + base // block)
    return np.concatenate(found) if found else np.empty(0, dtype=np.int64)


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


def list_fragments(mm, size, args):
    """Lập danh sách mảnh ứng viên (start, end, known_start, ns, nguồn)."""
    if args.raw:
        return [(0, size, False, None, "raw")]
    frags = []
    for e, ns in find_sos(mm, size, args.block_size, args):
        end = extend(mm, size, e, args.block_size, 0)
        if end and end - e >= args.min_size:
            frags.append((e, end, True, ns, "SOS"))

    merged = []                                   # vùng đã thuộc mảnh có SOS (gộp, đã sắp xếp)
    for s, t in sorted((s, t) for s, t, *_ in frags):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], t)
        else:
            merged.append([s, t])
    m_start = [s for s, _ in merged]
    m_end = [t for _, t in merged]

    block = args.block_size
    skip_until = 0
    for bi in candidate_blocks(mm, size, block, args.min_ff00):
        off = int(bi) * block
        if off < skip_until:
            continue
        j = bisect_right(m_start, off) - 1
        if j >= 0 and off < m_end[j]:
            continue
        arr = np.frombuffer(mm[off:off + block], dtype=np.uint8)
        ok, _ = chunk_check(arr, args.min_ff00)
        if ok:
            end = extend(mm, size, off, block, args.min_ff00)
            if end and end - off >= args.min_size:
                frags.append((off, end, False, None, "mồ côi"))
                skip_until = ((end + block - 1) // block) * block
    frags.sort(key=lambda f: f[0])
    return frags


# ----------------------------------------------------------------------------
# Tiến trình làm việc (dùng chung cho chế độ 1 tiến trình và pool)
# ----------------------------------------------------------------------------
_G = {}


def _init_worker(args):
    tablesets = [load_reference(r, args.quality) for r in args.ref]
    tablesets.append(standard_tableset(args.quality))
    f = open(args.input, "rb")
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    _G.update(args=args, ts=tablesets, mm=mm, f=f)
    if HAVE_NUMBA and not args.no_jit:
        _setup_jit(tablesets)


def _task(job):
    """Giải mã + dựng + lưu một mảnh. Trả (idx, các dòng log, các dòng báo cáo)."""
    idx, s, t, known, ns, src = job
    args, mm, tablesets = _G["args"], _G["mm"], _G["ts"]
    log, report = [], []
    log.append("\n[%d] offset 0x%x, %d byte, nguồn: %s" % (idx, s, t - s, src))
    data = bytes(mm[s:t])
    if args.dump_bin:
        with open(os.path.join(args.out, "frag_%010x.bin" % s), "wb") as fo:
            fo.write(data)
    pieces, notes = process_fragment(data, known, ns, tablesets, args)
    for nt in notes:
        log.append("    - " + nt)
    if not pieces:
        log.append("    -> không khôi phục được")
        return idx, log, report
    for j, pc in enumerate(pieces):
        img, info = render_piece(pc, args)
        nb = len(pc["coefs"])
        if img is None:
            log.append("    mảnh %d: %d khối, bỏ qua (%s)" % (j, nb, info))
            continue
        try:
            saved = save_jpeg(img, args.out, "frag_%010x_%d" % (s, j), args.jpeg_quality,
                              0 if args.jpeg_444 else 2)
        except Exception as ex:
            log.append("    mảnh %d: không lưu được JPEG (%s: %s)" % (j, type(ex).__name__, ex))
            continue
        names = ", ".join(n for n, _, _ in saved)
        log.append("    mảnh %d: %s, %s, bảng '%s' -> %s (%dx%d)" % (
            j, pc["scheme"], info, pc["ts"].name, names, img.width, img.height))
        for name, w, h in saved:
            report.append((name, s, t - s, pc["scheme"], pc["ts"].name, w, h))
    return idx, log, report


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
    ap.add_argument("--jpeg-quality", type=int, default=90, help="chất lượng JPEG đầu ra 1-100 (mặc định 90)")
    ap.add_argument("--jpeg-444", action="store_true", help="JPEG đầu ra giữ nguyên độ phân giải màu 4:4:4 (mặc định 4:2:0, nhẹ hơn)")
    ap.add_argument("-j", "--jobs", type=int, default=0, help="số tiến trình song song (mặc định: số lõi CPU)")
    ap.add_argument("--no-jit", action="store_true", help="không dùng numba (dùng Python thuần)")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.perf_counter()
    os.makedirs(args.out, exist_ok=True)
    size = os.path.getsize(args.input)
    if size == 0:
        raise SystemExit("Tệp rỗng.")

    _init_worker(args)
    if not HAVE_NUMBA and not args.no_jit:
        print("Gợi ý: cài numba (pip install numba) để giải mã Huffman nhanh hơn rất nhiều.")

    # ---- 1) lập danh sách mảnh ứng viên
    frags = list_fragments(_G["mm"], size, args)
    print("Tìm thấy %d mảnh ứng viên (>= %d byte). [quét: %.2fs, %s]" % (
        len(frags), args.min_size, time.perf_counter() - t0, "numba" if _JIT else "Python thuần"))

    # ---- 2) giải mã từng mảnh (song song)
    jobs = [(i,) + f for i, f in enumerate(frags)]
    nproc = args.jobs if args.jobs > 0 else (os.cpu_count() or 1)
    nproc = max(1, min(nproc, len(jobs)))
    report = []
    if nproc == 1:
        results = map(_task, jobs)
        pool = None
    else:
        pool = multiprocessing.Pool(nproc, initializer=_init_worker, initargs=(args,))
        results = pool.imap(_task, jobs, chunksize=1)
    try:
        for _, log, rep in results:
            for line in log:
                print(line)
            report.extend(rep)
    finally:
        if pool is not None:
            pool.close()
            pool.join()

    with open(os.path.join(args.out, "report.csv"), "w", encoding="utf-8") as fr:
        fr.write("file,offset,size,scheme,tables,width,height\n")
        for r in report:
            fr.write("%s,0x%x,%d,%s,%s,%d,%d\n" % r)
    print("\nXong: %d ảnh JPEG được lưu trong '%s' (%.2fs)." % (
        len(report), args.out, time.perf_counter() - t0))


if __name__ == "__main__":
    main()
