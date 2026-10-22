#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jpgscraper.py - Cài đặt Python của quy trình "JpgScraper" (Uzun & Sencar)
để khai thác (carve) các mảnh tệp JPEG "mồ côi" (không còn header) từ ảnh đĩa.

Quy trình (theo Fig. 2 của bài báo):
  1. Khôi phục JPEG có header đầy đủ (SOI..SOS..EOI) -> học tham số (Huffman,
     lấy mẫu màu, độ rộng, bảng lượng tử hoá) để ưu tiên khi giải mã mảnh mồ côi.
  2. Trong các khối còn lại: tìm header một phần bằng từ khoá SOS (SH1..SH5).
  3. Phần còn lại: phân loại từng khối 4 KiB là JPEG hay không bằng
     "byte-boundary metric" + OSCAR (ensemble).
  4. Giải mã mảnh bitstream không có header: thử các bộ bảng Huffman x cấu trúc
     MCU x pha, dựa vào tính tự đồng bộ của mã Huffman; đếm lỗi
     (invalid codeword / coefficient overflow) để chọn cấu hình & phát hiện
     điểm phân mảnh.
  5. Dựng ảnh: giải lượng tử, IDCT, dò độ rộng ảnh, định vị lại cột (dịch vòng),
     chỉnh sáng/màu (histogram stretching), kiểm tra "ảnh tự nhiên".

Phụ thuộc: numpy, Pillow.
Dùng:
  python jpgscraper.py carve disk.img -o out/
  python jpgscraper.py fragment frag.bin -o frag.png
  python jpgscraper.py selftest
"""
import argparse
import functools
import io
import json
import mmap
import os
import re
import struct
import sys
import tempfile
from collections import Counter, deque
from dataclasses import dataclass

import numpy as np
from PIL import Image

# ----------------------------------------------------------------------------
# Hằng số / tham số
# ----------------------------------------------------------------------------
BLOCK_DEFAULT = 4096          # kích thước cụm/khối
CHUNK_BYTES = 1024            # cửa sổ xác định cấu hình (1 KiB, theo bài báo)
SYNC_BYTES = 200              # vùng đồng bộ ban đầu (~125 byte trung bình)
SYNC_BITS = SYNC_BYTES * 8
MAX_INVALID = 2               # ngưỡng invalid codeword / 1 KiB
MAX_OVERFLOW = 5              # ngưỡng coefficient overflow / 1 KiB
OSCAR_THR = 2                 # ngưỡng số lần xuất hiện 0xFF00
MIN_FRAGMENT = 4096           # mảnh nhỏ hơn không dựng ảnh
MAX_FRAGMENT = 32 * 1024 * 1024
TOP_HT_SETS = 12

# Từ khoá header SOS (Table II của bài báo)
SOS_KW = [bytes.fromhex(h) for h in (
    "FFDA0008010100003F00",        # SH1 (xám - không hỗ trợ dựng)
    "FFDA000C03010002110311003F00",  # SH2 (phổ biến nhất)
    "FFDA000C03000001110211003F00",  # SH3
    "FFDA000C03000001000200003F00",  # SH4
    "FFDA000C03520047004200003F00",  # SH5
)]
SOS_RE = re.compile(b"|".join(re.escape(k) for k in SOS_KW))
MARKER_RE = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")

# ----------------------------------------------------------------------------
# Zigzag, bảng lượng tử mặc định, ma trận DCT
# ----------------------------------------------------------------------------
ZZ = []
for _s in range(15):
    _cells = [(i, _s - i) for i in range(max(0, _s - 7), min(_s, 7) + 1)]
    if _s % 2 == 0:
        _cells.reverse()
    ZZ += [i * 8 + j for i, j in _cells]
ZZ_ARR = np.array(ZZ)

_QY = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
       14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
       18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
       49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
_QC = [17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99,
       24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99] + [99] * 32


def scaled_qt_zz(base, qf=80):
    """Bảng lượng tử chuẩn (Annex K) scale theo QF, trả về thứ tự zigzag."""
    scale = 5000 // qf if qf < 50 else 200 - 2 * qf
    nat = [min(255, max(1, (v * scale + 50) // 100)) for v in base]
    return [nat[ZZ[k]] for k in range(64)]


DEFAULT_QT = (scaled_qt_zz(_QY), scaled_qt_zz(_QC))

_C = np.zeros((8, 8), np.float32)
for _u in range(8):
    for _x in range(8):
        _a = np.sqrt(1 / 8) if _u == 0 else np.sqrt(2 / 8)
        _C[_u, _x] = _a * np.cos((2 * _x + 1) * _u * np.pi / 16)
DCT_C, DCT_CT = _C, _C.T.copy()

# ----------------------------------------------------------------------------
# Bảng Huffman
# ----------------------------------------------------------------------------
def _r(a, b):
    return list(range(a, b + 1))


STD_DC_L = ([0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0], _r(0, 11))
STD_DC_C = ([0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0], _r(0, 11))
STD_AC_L = ([0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d],
            [0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41,
             0x06, 0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91,
             0xa1, 0x08, 0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0, 0x24,
             0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a,
             0x25, 0x26, 0x27, 0x28, 0x29, 0x2a] + _r(0x34, 0x3a) + _r(0x43, 0x4a)
            + _r(0x53, 0x5a) + _r(0x63, 0x6a) + _r(0x73, 0x7a) + _r(0x83, 0x8a)
            + _r(0x92, 0x9a) + _r(0xa2, 0xaa) + _r(0xb2, 0xba) + _r(0xc2, 0xca)
            + _r(0xd2, 0xda) + _r(0xe1, 0xea) + _r(0xf1, 0xfa))
STD_AC_C = ([0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77],
            [0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12,
             0x41, 0x51, 0x07, 0x61, 0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14,
             0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0, 0x15,
             0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17,
             0x18, 0x19, 0x1a, 0x26, 0x27, 0x28, 0x29, 0x2a] + _r(0x35, 0x3a)
            + _r(0x43, 0x4a) + _r(0x53, 0x5a) + _r(0x63, 0x6a) + _r(0x73, 0x7a)
            + _r(0x82, 0x8a) + _r(0x92, 0x9a) + _r(0xa2, 0xaa) + _r(0xb2, 0xba)
            + _r(0xc2, 0xca) + _r(0xd2, 0xda) + _r(0xe2, 0xea) + _r(0xf2, 0xfa))
for _b, _v in (STD_DC_L, STD_DC_C, STD_AC_L, STD_AC_C):
    assert sum(_b) == len(_v)


class HuffLUT:
    """Bảng tra 16 bit -> (độ dài, ký hiệu). length==0 nghĩa là codeword không hợp lệ."""

    def __init__(self, bits, vals):
        length = np.zeros(65536, np.uint8)
        sym = np.zeros(65536, np.uint8)
        code, k = 0, 0
        for L in range(1, 17):
            for _ in range(bits[L - 1]):
                lo, hi = code << (16 - L), (code + 1) << (16 - L)
                if hi > 65536 or k >= len(vals):
                    raise ValueError("bảng Huffman không hợp lệ")
                length[lo:hi] = L
                sym[lo:hi] = vals[k]
                k += 1
                code += 1
            code <<= 1
        self.length = length.tolist()
        self.sym = sym.tolist()


@functools.lru_cache(maxsize=256)
def _lut(bits, vals):
    return HuffLUT(bits, vals)


class HuffSet:
    def __init__(self, dc_l, dc_c, ac_l, ac_c, name=""):
        self.raw = (tuple(map(tuple, dc_l)), tuple(map(tuple, dc_c)),
                    tuple(map(tuple, ac_l)), tuple(map(tuple, ac_c)))
        self.dc_l, self.dc_c, self.ac_l, self.ac_c = (_lut(tuple(b), tuple(v)) for b, v in self.raw)
        self.name = name

    @property
    def key(self):
        return self.raw


STD_HS = HuffSet(STD_DC_L, STD_DC_C, STD_AC_L, STD_AC_C, "JPEG Annex K (HT-1)")

# ----------------------------------------------------------------------------
# Phân tích header (đầy đủ hoặc một phần)
# ----------------------------------------------------------------------------
def parse_header_blob(buf):
    """Quét các marker DQT/DHT/SOF/DRI trong buf, bản xuất hiện SAU CÙNG thắng
    (để bỏ qua bảng của ảnh thumbnail trong APP1)."""
    info = {"qt": {}, "ht": {}, "sof": None, "dri": 0}
    for m in re.finditer(rb"\xff[\xdb\xc4\xc0\xc1\xdd]", buf):
        s = m.start()
        if s + 4 > len(buf):
            continue
        mk = buf[s + 1]
        L = struct.unpack(">H", buf[s + 2:s + 4])[0]
        end = s + 2 + L
        if L < 2 or end > len(buf):
            continue
        seg = buf[s + 4:end]
        try:
            if mk == 0xDB:
                i = 0
                while i < len(seg):
                    pq, tq = seg[i] >> 4, seg[i] & 15
                    n = 64 * (2 if pq else 1)
                    t = seg[i + 1:i + 1 + n]
                    if len(t) < n:
                        break
                    vals = list(t) if not pq else [(t[2 * j] << 8) | t[2 * j + 1] for j in range(64)]
                    info["qt"][tq] = vals  # thứ tự zigzag
                    i += 1 + n
            elif mk == 0xC4:
                i = 0
                while i + 17 <= len(seg):
                    tc, th = seg[i] >> 4, seg[i] & 15
                    bits = list(seg[i + 1:i + 17])
                    tot = sum(bits)
                    vals = list(seg[i + 17:i + 17 + tot])
                    if len(vals) != tot:
                        break
                    info["ht"][(tc, th)] = (bits, vals)
                    i += 17 + tot
            elif mk in (0xC0, 0xC1):
                P, Y, X, nf = seg[0], (seg[1] << 8) | seg[2], (seg[3] << 8) | seg[4], seg[5]
                comps = [(seg[6 + 3 * j], seg[7 + 3 * j] >> 4, seg[7 + 3 * j] & 15, seg[8 + 3 * j])
                         for j in range(nf)]
                info["sof"] = {"w": X, "h": Y, "comps": comps}
            elif mk == 0xDD:
                info["dri"] = (seg[0] << 8) | seg[1]
        except Exception:
            continue
    return info


def hdr_huffset(hdr):
    ht = hdr["ht"]
    if (0, 0) in ht and (1, 0) in ht:
        dc_c = ht.get((0, 1), ht[(0, 0)])
        ac_c = ht.get((1, 1), ht[(1, 0)])
        try:
            return HuffSet(ht[(0, 0)], dc_c, ht[(1, 0)], ac_c, "from-header")
        except ValueError:
            return None
    return None


def hdr_samp(hdr):
    sof = hdr.get("sof")
    if sof and len(sof["comps"]) == 3:
        _, h, v, _ = sof["comps"][0]
        if (h, v) in ((1, 1), (2, 1), (1, 2), (2, 2)):
            return (h, v)
    return None


class ParamDict:
    """Từ điển tham số: Huffman, lấy mẫu, bảng lượng tử, độ rộng, sắp theo tần suất."""

    def __init__(self):
        self.ht = {STD_HS.key: [1000, STD_HS]}
        self.samp = Counter({(2, 2): 3, (2, 1): 2, (1, 1): 1})
        self.widths = Counter()
        self.qt = {}

    def learn(self, hdr):
        hs = hdr_huffset(hdr)
        if hs:
            self.ht.setdefault(hs.key, [0, hs])[0] += 1000 if hs.key != STD_HS.key else 0
            self.ht[hs.key][0] += 1
        sp = hdr_samp(hdr)
        if sp:
            self.samp[sp] += 1000
            if hdr["sof"]["w"]:
                self.widths[hdr["sof"]["w"]] += 1
            if 0 in hdr["qt"] and 1 in hdr["qt"]:
                self.qt[sp] = (hdr["qt"][0], hdr["qt"][1])

    def ht_sets(self):
        return [v[1] for v in sorted(self.ht.values(), key=lambda x: -x[0])][:TOP_HT_SETS]

    def samp_order(self):
        return [s for s, _ in self.samp.most_common() if s != (1, 2)] or [(2, 2), (2, 1), (1, 1)]

    def qtables(self, samp):
        return self.qt.get(samp, DEFAULT_QT)


# ----------------------------------------------------------------------------
# Phân loại dữ liệu JPEG: byte-boundary metric + OSCAR (Sec. III-C)
# ----------------------------------------------------------------------------
def _shift_variants(arr):
    bits = np.unpackbits(arr)
    n = len(bits)
    for s in range(8):
        m = (n - s) // 8
        if m < 2:
            continue
        yield s, np.packbits(bits[s:s + 8 * m])


def _bad_marker_count(b):
    ff = b[:-1] == 0xFF
    nx = b[1:]
    bad = ff & (nx != 0) & ~((nx >= 0xD0) & (nx <= 0xD7)) & (nx != 0xD9)
    return int(bad.sum())


def valid_shifts(arr):
    """Các độ lệch bit (0..7) mà tại đó không có mẫu FFxx bị cấm."""
    return [(s, b) for s, b in _shift_variants(arr) if _bad_marker_count(b) == 0]


def oscar_ok(b, thr=OSCAR_THR):
    ff = b[:-1] == 0xFF
    nx = b[1:]
    if int((ff & (nx == 0)).sum()) < thr:
        return False
    if int((ff & (nx == 0xD9)).sum()) > 1:
        return False
    rst = nx[ff & (nx >= 0xD0) & (nx <= 0xD7)].astype(int)
    if len(rst) > 1 and not np.all(((rst[:-1] - 0xD0 + 1) % 8) == (rst[1:] - 0xD0)):
        return False
    return True


def is_jpeg_chunk(chunk, thr=OSCAR_THR):
    arr = np.frombuffer(chunk, np.uint8)
    vs = valid_shifts(arr)
    if not vs:
        return False
    if len(vs) == 1:
        return True
    return any(oscar_ok(b, thr) for _, b in vs)


# ----------------------------------------------------------------------------
# Giải mã bitstream Huffman từ vị trí bit bất kỳ
# ----------------------------------------------------------------------------
@dataclass
class ScanResult:
    comps: list
    bpos: list
    coefs: np.ndarray
    errs: list  # (bit, kind) kind 0=invalid codeword, 1=overflow


def mcu_pattern(samp):
    return (0,) * (samp[0] * samp[1]) + (1, 2)


def decode_scan(data, mcu, phase, hs, start_bit=0):
    buf = data + b"\x00" * 8
    nbits = len(data) * 8
    n = len(mcu)
    tabs = {0: (hs.dc_l, hs.ac_l), 1: (hs.dc_c, hs.ac_c), 2: (hs.dc_c, hs.ac_c)}
    fb = int.from_bytes
    pos, ci = start_bit, phase
    comps, bpos, errs = [], [], []
    bi_l, k_l, v_l = [], [], []
    while pos < nbits - 8:
        comp = mcu[ci % n]
        dc, ac = tabs[comp]
        start = pos
        i = pos >> 3
        p16 = (fb(buf[i:i + 4], "big") >> (16 - (pos & 7))) & 0xFFFF
        l = dc.length[p16]
        s = dc.sym[p16]
        if l == 0 or s > 11:
            errs.append((pos, 0))
            pos += 1
            continue
        pos += l
        val = 0
        if s:
            i = pos >> 3
            bits = (fb(buf[i:i + 4], "big") >> (32 - s - (pos & 7))) & ((1 << s) - 1)
            val = bits if bits >= (1 << (s - 1)) else bits - (1 << s) + 1
            pos += s
        bk, bv = [0], [val]
        k = 1
        status = 0  # 0 ok, 1 overflow, 2 invalid
        alen, asym = ac.length, ac.sym
        while k < 64:
            i = pos >> 3
            p16 = (fb(buf[i:i + 4], "big") >> (16 - (pos & 7))) & 0xFFFF
            l = alen[p16]
            if l == 0:
                status = 2
                break
            rs = asym[p16]
            pos += l
            r, s = rs >> 4, rs & 15
            if s == 0:
                if r == 15:
                    k += 16
                    if k > 63:
                        status = 1
                        break
                    continue
                if r == 0:
                    break  # EOB
                status = 2
                break
            k += r
            if k > 63:
                status = 1
                break
            i = pos >> 3
            bits = (fb(buf[i:i + 4], "big") >> (32 - s - (pos & 7))) & ((1 << s) - 1)
            val = bits if bits >= (1 << (s - 1)) else bits - (1 << s) + 1
            pos += s
            bk.append(k)
            bv.append(val)
            k += 1
        if status == 2:
            errs.append((pos, 0))
            pos = start + 1
            continue
        if pos > nbits:
            break
        if status == 1:
            errs.append((pos, 1))
        b = len(comps)
        comps.append(comp)
        bpos.append(start)
        bi_l.extend([b] * len(bk))
        k_l.extend(bk)
        v_l.extend(bv)
        ci += 1
    coefs = np.zeros((len(comps), 64), np.int16)
    if bi_l:
        coefs[np.array(bi_l), np.array(k_l)] = np.array(v_l, np.int16)
    return ScanResult(comps, bpos, coefs, errs)


def _count_errs(errs, lo_bit, hi_bit=None):
    inv = ovf = 0
    for p, kd in errs:
        if p >= lo_bit and (hi_bit is None or p < hi_bit):
            if kd == 0:
                inv += 1
            else:
                ovf += 1
    return inv, ovf


def select_settings(data, cands, aligned):
    """Thử mọi (bộ Huffman, lấy mẫu, pha) trên 1 KiB đầu; chọn cấu hình ít lỗi nhất."""
    test = data[:CHUNK_BYTES + SYNC_BYTES]
    if len(test) < 400:
        return None
    sync = 0 if aligned else SYNC_BITS
    best, best_score = None, None
    for hs, samp in cands:
        mcu = mcu_pattern(samp)
        for ph in ([0] if aligned else range(len(mcu))):
            r = decode_scan(test, mcu, ph, hs)
            if len(r.comps) < 8:
                continue
            inv, ovf = _count_errs(r.errs, sync)
            score = inv * 2 + ovf
            if best_score is None or score < best_score:
                best_score, best = score, (hs, samp, ph, inv, ovf)
    if best is None:
        return None
    hs, samp, ph, inv, ovf = best
    if inv > MAX_INVALID or ovf > MAX_OVERFLOW:
        return None
    return hs, samp, ph


def find_cut(errs, sync_bit):
    """Điểm phân mảnh: lỗi đầu tiên mở ra cửa sổ 1 KiB vượt ngưỡng lỗi."""
    ee = [(p, k) for p, k in errs if p >= sync_bit]
    win = CHUNK_BYTES * 8
    j = 0
    for i, (p, _) in enumerate(ee):
        if j < i:
            j = i
        while j < len(ee) and ee[j][0] < p + win:
            j += 1
        sub = ee[i:j]
        inv = sum(1 for _, k in sub if k == 0)
        ovf = len(sub) - inv
        if inv > MAX_INVALID or ovf > MAX_OVERFLOW:
            return p
    return None


# ----------------------------------------------------------------------------
# Chuẩn bị luồng: gỡ byte stuffing, tách theo RST, dừng ở EOI
# ----------------------------------------------------------------------------
def prepare_stream(raw, shift):
    arr = np.frombuffer(raw, np.uint8)
    if shift:
        bits = np.unpackbits(arr)
        m = (len(bits) - shift) // 8
        b = np.packbits(bits[shift:shift + 8 * m])
    else:
        b = arr
    ff = b[:-1] == 0xFF
    nx = b[1:]
    eoi = np.nonzero(ff & (nx == 0xD9))[0]
    end = int(eoi[0]) if len(eoi) else len(b)
    eoi_end = end + 2 if len(eoi) else None
    b = b[:end]
    if len(b) < 2:
        return [], eoi_end
    ff = b[:-1] == 0xFF
    nx = b[1:]
    stuff = np.nonzero(ff & (nx == 0))[0] + 1
    rst = np.nonzero(ff & (nx >= 0xD0) & (nx <= 0xD7))[0]
    keep = np.ones(len(b), bool)
    keep[stuff] = False
    keep[rst] = False
    keep[rst + 1] = False
    starts = [0] + [int(r) + 2 for r in rst]
    ends = [int(r) for r in rst] + [len(b)]
    segs = []
    for s, e in zip(starts, ends):
        if e <= s:
            continue
        idx = np.arange(s, e)[keep[s:e]]
        segs.append({"data": b[idx].tobytes(), "src": idx})
    return segs, eoi_end


# ----------------------------------------------------------------------------
# Dựng ảnh
# ----------------------------------------------------------------------------
def idct_blocks(coef, q_zz):
    n = coef.shape[0]
    nat = np.zeros((n, 64), np.float32)
    nat[:, ZZ_ARR] = coef.astype(np.float32) * np.asarray(q_zz, np.float32)
    return DCT_CT @ nat.reshape(n, 8, 8) @ DCT_C + 128.0


def accumulate_dc(mc, nY):
    """Khôi phục DC tuyệt đối từ DC vi sai (theo từng thành phần)."""
    mc = mc.astype(np.int32)
    mc[:, :nY, 0] = np.cumsum(mc[:, :nY, 0].reshape(-1)).reshape(-1, nY)
    mc[:, nY, 0] = np.cumsum(mc[:, nY, 0])
    mc[:, nY + 1, 0] = np.cumsum(mc[:, nY + 1, 0])
    return mc


def planes_from_mcus(mcus, samp, qy, qc):
    N, per, _ = mcus.shape
    h, v = samp
    nY = per - 2
    y = idct_blocks(mcus[:, :nY].reshape(-1, 64), qy).reshape(N, v, h, 8, 8)
    ytiles = y.transpose(0, 1, 3, 2, 4).reshape(N, v * 8, h * 8)
    cb = idct_blocks(mcus[:, nY], qc)
    cr = idct_blocks(mcus[:, nY + 1], qc)
    up = lambda c: np.repeat(np.repeat(c, v, axis=1), h, axis=2)
    return ytiles, up(cb), up(cr)


def detect_width(ytiles, known=()):
    """Dò độ rộng (tính theo MCU) bằng độ gián đoạn giữa MCU i và i+W."""
    N = len(ytiles)
    minw = 4
    maxw = min(N // 2, 1500)
    if maxw < minw + 2:
        return None, False
    bottom, top = ytiles[:, -1, :], ytiles[:, 0, :]
    D = {}
    for W in range(minw, maxw + 1):
        D[W] = float(np.abs(bottom[:N - W] - top[W:]).mean())
    dmin = min(D.values())
    cands = [W for W, d in D.items() if d <= dmin * 1.03]
    W = min(cands)
    for kw in known:
        if kw in D and D[kw] <= dmin * 1.10:
            W = kw
            break
    ordered = sorted(D.values())
    med = ordered[len(ordered) // 2]
    return W, dmin < 0.8 * med


def detect_offset(ytiles, W):
    """Tìm vị trí cột bắt đầu hàng ảnh thật (dịch vòng) -> trả về pad."""
    N = len(ytiles)
    if N < 3 * W:
        return 0
    e = np.abs(ytiles[:-1, :, -1] - ytiles[1:, :, 0]).mean(axis=1)  # e[i-1]: i-1 | i
    sc = np.zeros(W)
    cnt = np.zeros(W)
    idx = (np.arange(1, N)) % W
    np.add.at(sc, idx, e)
    np.add.at(cnt, idx, 1)
    sc /= np.maximum(cnt, 1)
    cstar = int(np.argmax(sc))
    return (W - cstar) % W


def compose(tiles, W, pad, fill=128.0):
    N, th, tw = tiles.shape
    rows = -(-(N + pad) // W)
    full = np.full((rows * W, th, tw), fill, np.float32)
    full[pad:pad + N] = tiles
    return full.reshape(rows, W, th, tw).transpose(0, 2, 1, 3).reshape(rows * th, W * tw)


def render(mcus, samp, qt, W, pad, crop=None):
    ytiles, cb, cr = planes_from_mcus(mcus, samp, *qt)
    # chỉnh sáng/màu (histogram stretching; chroma theo gray-world)
    lo, hi = np.percentile(ytiles, (1, 99))
    ytiles = (ytiles - lo) * 255.0 / max(hi - lo, 1.0)
    cb = cb - (cb.mean() - 128.0)
    cr = cr - (cr.mean() - 128.0)
    Y, Cb, Cr = (compose(t, W, pad) for t in (ytiles, cb, cr))
    R = Y + 1.402 * (Cr - 128)
    G = Y - 0.344136 * (Cb - 128) - 0.714136 * (Cr - 128)
    B = Y + 1.772 * (Cb - 128)
    rgb = np.clip(np.stack([R, G, B], -1), 0, 255).astype(np.uint8)
    if crop:
        rgb = rgb[:crop[1], :crop[0]]
    return Image.fromarray(rgb)


def looks_natural(mcus, nY):
    """Kiểm tra gần đúng 'thống kê ảnh tự nhiên' (thay cho bộ phân loại wavelet
    của bài báo): phần lớn hệ số AC bằng 0 và biên độ giảm theo tần số."""
    yb = mcus[:, :nY].reshape(-1, 64).astype(np.float32)
    if len(yb) < 8:
        return False
    zero_frac = float((yb[:, 1:] == 0).mean())
    lowE = np.abs(yb[:, 1:6]).mean()
    highE = np.abs(yb[:, 30:]).mean() + 1e-6
    return zero_frac > 0.45 and lowE / highE > 3.0


# ----------------------------------------------------------------------------
# Khai thác một mảnh (không header hoặc header một phần)
# ----------------------------------------------------------------------------
@dataclass
class Carved:
    image: Image.Image
    consumed: int
    width_px: int
    samp: tuple
    ht_name: str
    n_mcu: int
    confident: bool


def _align_consumed(abs_start, rel, block):
    c = ((abs_start + rel) // block) * block - abs_start
    return c if c > 0 else rel


def carve_stream(raw, params, hdr=None, abs_start=0, block=BLOCK_DEFAULT,
                 width_hint=None, log=lambda *a: None):
    aligned = bool(hdr and hdr.get("entropy_start") is not None)
    raw = raw[:MAX_FRAGMENT]
    if len(raw) < 1024:
        return None
    if aligned:
        shift = 0
    else:
        vs = valid_shifts(np.frombuffer(raw[:65536], np.uint8))
        if not vs:
            return None
        shift = 0 if any(s == 0 for s, _ in vs) else vs[0][0]
    segs, eoi_end = prepare_stream(raw, shift)
    segs = [s for s in segs if len(s["data"]) >= 16]
    if not segs:
        return None

    # ứng viên cấu hình, ưu tiên tham số lấy từ header / ảnh đã khôi phục
    h_hs = hdr_huffset(hdr) if hdr else None
    h_sp = hdr_samp(hdr) if hdr else None
    hts = ([h_hs] if h_hs else []) + [x for x in params.ht_sets() if not h_hs or x.key != h_hs.key]
    sps = [h_sp] if h_sp else params.samp_order()
    cands = [(hs, sp) for hs in hts for sp in sps]
    sel = select_settings(segs[0]["data"], cands, aligned)
    if not sel:
        return None
    hs, samp, phase = sel
    mcu = mcu_pattern(samp)
    per = len(mcu)
    nY = per - 2

    seg_mcus, consumed, cut = [], None, False
    for si, sg in enumerate(segs):
        data, src = sg["data"], sg["src"]
        ph = phase if si == 0 else 0
        res = decode_scan(data, mcu, ph, hs)
        if not res.comps:
            break
        sync_bit = 0
        if si == 0 and not aligned:
            early = [p for p, _ in res.errs if p < SYNC_BITS]
            sync_bit = (max(early) + 1) if early else 0
        cut_bit = find_cut(res.errs, sync_bit)
        end_bit = len(data) * 8
        if cut_bit is not None:
            ci_byte = min(cut_bit >> 3, len(src) - 1)
            rel = int(src[ci_byte]) + (1 if shift else 0)
            consumed = _align_consumed(abs_start, rel, block)
            end_bit = int(np.searchsorted(src, consumed)) * 8
            cut = True
        bp = np.asarray(res.bpos)
        ok = np.nonzero((bp >= sync_bit) & (bp < end_bit))[0]
        if len(ok):
            first, last = int(ok[0]), int(ok[-1])
            if si == 0 and not aligned and sync_bit > 0:
                first += per  # biên an toàn sau điểm đồng bộ
            j0 = first + ((-(ph + first)) % per)
            nm = (last + 1 - j0) // per
            if nm > 0:
                seg_mcus.append(accumulate_dc(res.coefs[j0:j0 + nm * per].reshape(nm, per, 64), nY))
        if cut:
            break
    if not seg_mcus:
        return None
    mcus = np.concatenate(seg_mcus, axis=0)
    if len(mcus) < 8 or not looks_natural(mcus, nY):
        return None

    if consumed is None:
        if eoi_end is not None:
            consumed = _align_consumed(abs_start, eoi_end + (1 if shift else 0), block)
            if consumed < eoi_end:
                consumed = eoi_end
        else:
            consumed = len(raw)
    consumed = max(1, min(consumed, len(raw)))

    # dựng ảnh
    qt = DEFAULT_QT
    crop = None
    if hdr and hdr.get("qt", {}).get(0) and hdr["qt"].get(1):
        qt = (hdr["qt"][0], hdr["qt"][1])
    else:
        qt = params.qtables(samp)
    mcu_w = 8 * samp[0]
    confident = True
    if hdr and hdr.get("sof") and hdr["sof"]["w"]:
        W = -(-hdr["sof"]["w"] // mcu_w)
        pad = 0
        crop = (hdr["sof"]["w"], hdr["sof"]["h"])
    else:
        ytiles, _, _ = planes_from_mcus(mcus, samp, *qt)
        known = [-(-w // mcu_w) for w, _ in params.widths.most_common(50)]
        if width_hint:
            W, confident = -(-width_hint // mcu_w), True
        else:
            W, confident = detect_width(ytiles, known)
        if W is None:
            W, confident = min(len(mcus), 64), False
            pad = 0
        else:
            pad = detect_offset(ytiles, W) if confident else 0
    img = render(mcus, samp, qt, W, pad, crop)
    return Carved(img, consumed, W * mcu_w, samp, hs.name, len(mcus), confident)


# ----------------------------------------------------------------------------
# Bước 1: khôi phục JPEG đầy đủ header
# ----------------------------------------------------------------------------
def carve_full(mm, pos, maxlen=64 * 1024 * 1024):
    """Trả về (end, complete, header_bytes) hoặc None."""
    size = len(mm)
    i = pos + 2
    try:
        while True:
            if mm[i] != 0xFF:
                return None
            m = mm[i + 1]
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            if m == 0xD9:
                return None
            L = struct.unpack(">H", mm[i + 2:i + 4])[0]
            if L < 2:
                return None
            if m == 0xDA:
                hdr_end = i + 2 + L
                break
            i += 2 + L
    except (IndexError, struct.error):
        return None
    hdr = bytes(mm[pos:hdr_end])
    mk = MARKER_RE.search(mm, hdr_end, min(size, pos + maxlen))
    if mk is None:
        return None
    if mm[mk.start() + 1] == 0xD9:
        return mk.start() + 2, True, hdr
    return mk.start(), False, hdr


# ----------------------------------------------------------------------------
# Pipeline trên ảnh đĩa
# ----------------------------------------------------------------------------
def run_carve(path, outdir, block=BLOCK_DEFAULT, min_fragment=MIN_FRAGMENT,
              verbose=False, learn_from=()):
    log = (lambda *a: print(*a, file=sys.stderr)) if verbose else (lambda *a: None)
    os.makedirs(outdir, exist_ok=True)
    params = ParamDict()
    for p in learn_from:
        with open(p, "rb") as f:
            params.learn(parse_header_blob(f.read(1 << 20)))
    report = []
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        nblk = -(-size // block)
        status = np.zeros(nblk, np.uint8)  # 0 ứng viên, 1 đã khôi phục, 2 trống

        # --- khối trống (>=1 KiB toàn 0x00 hoặc 0xFF) ---
        z, o = b"\x00" * 1024, b"\xff" * 1024
        for bi in range(nblk):
            blk = mm[bi * block:(bi + 1) * block]
            if z in blk or o in blk:
                status[bi] = 2

        # --- bước 1: JPEG đầy đủ header ---
        pos, n_full = 0, 0
        while True:
            pos = mm.find(b"\xff\xd8\xff", pos)
            if pos < 0:
                break
            if pos % block == 0 and status[pos // block] != 1:
                r = carve_full(mm, pos)
                if r:
                    end, complete, hdr = r
                    params.learn(parse_header_blob(hdr))
                    name = os.path.join(outdir, f"full_{pos:012x}.jpg")
                    with open(name, "wb") as g:
                        g.write(mm[pos:end])
                    b0, b1 = pos // block, -(-end // block) if complete else end // block
                    status[b0:b1] = 1
                    report.append({"type": "full" if complete else "full-truncated",
                                   "offset": pos, "length": end - pos, "file": os.path.basename(name)})
                    n_full += 1
                    log(f"[full] 0x{pos:x} len={end - pos} complete={complete}")
                    pos = max(end, pos + 1)
                    continue
            pos += 1
        log(f"đã khôi phục {n_full} ảnh đầy đủ header; ht sets={len(params.ht)}")

        # --- bước 2/3: các đoạn còn lại ---
        runs, bi = [], 0
        while bi < nblk:
            if status[bi] == 0:
                bj = bi
                while bj < nblk and status[bj] == 0:
                    bj += 1
                runs.append((bi * block, min(bj * block, size)))
                bi = bj
            else:
                bi += 1

        def save(kind, abs_off, cv):
            name = f"{kind}_{abs_off:012x}.png"
            cv.image.save(os.path.join(outdir, name))
            report.append({"type": kind, "offset": abs_off, "length": cv.consumed,
                           "file": name, "width_px": cv.width_px, "sampling": list(cv.samp),
                           "huffman": cv.ht_name, "mcus": cv.n_mcu,
                           "width_confident": cv.confident})
            log(f"[{kind}] 0x{abs_off:x} consumed={cv.consumed} width={cv.width_px}px "
                f"{cv.ht_name} samp={cv.samp} conf={cv.confident}")

        for rs, re_ in runs:
            cur = rs
            while re_ - cur >= min_fragment:
                buf = mm[cur:re_]
                hit = None
                for m in SOS_RE.finditer(buf):
                    if m.group(0) != SOS_KW[0]:  # bỏ SH1 (ảnh xám)
                        hit = m
                        break
                lim = hit.start() if hit else len(buf)
                # tìm đoạn khối JPEG đầu tiên trước vị trí header
                a = b = None
                for c in range(0, lim - block + 1, block):
                    ok = is_jpeg_chunk(buf[c:c + block])
                    if ok and a is None:
                        a = c
                    if not ok and a is not None:
                        b = c
                        break
                if a is not None and b is None:
                    b = (lim // block) * block
                if a is not None and b - a >= min_fragment:
                    cv = carve_stream(buf[a:b], params, None, cur + a, block, log=log)
                    if cv:
                        save("orphan", cur + a, cv)
                        cur += a + cv.consumed
                    else:
                        cur += b
                    continue
                if hit:
                    o = hit.start()
                    pre = buf[max(0, o - 65536):o]
                    h = parse_header_blob(pre + buf[o:o + len(hit.group(0))])
                    h["entropy_start"] = o + len(hit.group(0))
                    cv = carve_stream(buf[h["entropy_start"]:], params, h,
                                      cur + h["entropy_start"], block, log=log)
                    if cv:
                        save("partial", cur + o, cv)
                        params.learn(h)
                        cur += h["entropy_start"] + cv.consumed
                    else:
                        cur += h["entropy_start"]
                    continue
                break

    with open(os.path.join(outdir, "report.json"), "w", encoding="utf-8") as g:
        json.dump(report, g, indent=2, ensure_ascii=False)
    return report


# ----------------------------------------------------------------------------
# Chế độ tự kiểm tra
# ----------------------------------------------------------------------------
def _synthetic_photo(w=640, h=480, seed=1):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.stack([
        128 + 100 * np.sin(xx / 61.0 + yy / 97.0),
        128 + 90 * np.cos(yy / 53.0 - xx / 131.0),
        128 + 80 * np.sin((xx + yy) / 83.0)], -1)
    for _ in range(25):
        cx, cy, r = rng.integers(0, w), rng.integers(0, h), rng.integers(20, 80)
        col = rng.integers(20, 235, 3)
        m = (xx - cx) ** 2 + (yy - cy) ** 2 < r * r
        img[m] = col
    img += rng.normal(0, 4, img.shape)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def selftest():
    block = 4096
    tmp = tempfile.mkdtemp(prefix="jpgscraper_")
    rng = np.random.default_rng(0)
    noise = lambda n: rng.integers(0, 256, n, dtype=np.uint8).tobytes()
    zeros = lambda n: b"\x00" * n

    def jpg(seed, w=640, h=480):
        bio = io.BytesIO()
        _synthetic_photo(w, h, seed).save(bio, "JPEG", quality=80)
        return bio.getvalue()

    j1, j2, j3 = jpg(1), jpg(2), jpg(3, 800, 600)
    pad = lambda b: b + zeros(-len(b) % block)
    disk = bytearray()
    disk += noise(block * 5)
    disk += pad(j1)                                  # ảnh đầy đủ header
    disk += zeros(block * 3)
    disk += pad(j2)[block * 3:]                      # mồ côi: mất 3 khối đầu (kể cả header)
    disk += noise(block * 4)
    broken = b"\x00" * 4 + j3[4:]                    # header bị phá nhưng còn SOS
    disk += pad(broken)                              # header một phần
    disk += zeros(block * 2)
    p = os.path.join(tmp, "disk.img")
    with open(p, "wb") as f:
        f.write(disk)
    print(f"[selftest] ảnh đĩa giả lập: {p} ({len(disk)} byte)")
    out = os.path.join(tmp, "out")
    rep = run_carve(p, out, block, verbose=True)
    kinds = Counter(r["type"] for r in rep)
    print("[selftest] kết quả:", dict(kinds), "->", out)
    ok = kinds["full"] >= 1 and kinds["orphan"] >= 1 and kinds["partial"] >= 1
    print("[selftest]", "ĐẠT" if ok else "CHƯA ĐẠT")
    return 0 if ok else 1


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="JpgScraper (Python) - carve mảnh JPEG mồ côi")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("carve", help="khai thác toàn bộ ảnh đĩa/tệp raw")
    c.add_argument("image")
    c.add_argument("-o", "--out", default="jpgscraper_out")
    c.add_argument("--block-size", type=int, default=BLOCK_DEFAULT)
    c.add_argument("--min-fragment", type=int, default=MIN_FRAGMENT)
    c.add_argument("--learn-from", nargs="*", default=[], help="JPEG mẫu để học bảng Huffman/QT")
    c.add_argument("-v", "--verbose", action="store_true")
    f = sub.add_parser("fragment", help="dựng ảnh từ một mảnh JPEG đơn lẻ")
    f.add_argument("file")
    f.add_argument("-o", "--out", default="fragment.png")
    f.add_argument("--offset", type=int, default=0)
    f.add_argument("--length", type=int, default=None)
    f.add_argument("--width", type=int, default=None, help="độ rộng ảnh (pixel) nếu đã biết")
    f.add_argument("--skip-classify", action="store_true")
    sub.add_parser("selftest", help="tự kiểm tra bằng dữ liệu giả lập")
    a = ap.parse_args()

    if a.cmd == "selftest":
        sys.exit(selftest())
    if a.cmd == "carve":
        rep = run_carve(a.image, a.out, a.block_size, a.min_fragment, a.verbose, a.learn_from)
        print(json.dumps(Counter(r["type"] for r in rep), ensure_ascii=False))
        print("Báo cáo:", os.path.join(a.out, "report.json"))
        return
    if a.cmd == "fragment":
        with open(a.file, "rb") as fh:
            fh.seek(a.offset)
            raw = fh.read(a.length) if a.length else fh.read()
        if not a.skip_classify:
            nchunks = max(1, len(raw) // BLOCK_DEFAULT)
            pos = sum(is_jpeg_chunk(raw[i * BLOCK_DEFAULT:(i + 1) * BLOCK_DEFAULT]) for i in range(nchunks))
            print(f"Khối 4 KiB được phân loại là JPEG: {pos}/{nchunks}")
        cv = carve_stream(raw, ParamDict(), None, 0, BLOCK_DEFAULT, a.width,
                          log=lambda *x: print(*x, file=sys.stderr))
        if not cv:
            print("Không dựng được ảnh (không phải JPEG baseline hoặc cấu hình không có trong từ điển).")
            sys.exit(2)
        cv.image.save(a.out)
        print(f"Đã lưu {a.out}: rộng≈{cv.width_px}px, lấy mẫu={cv.samp}, {cv.ht_name}, "
              f"tin cậy độ rộng={cv.confident}")


if __name__ == "__main__":
    main()
