import subprocess, os, re, numpy as np
from PIL import Image
from textwrap import dedent
from controllers import jpgfix2, MCUDecode

copyright_ = "FFD8FFE1007C45786966000049492A000800000003000E010200270000003200000012010300010000000100000031010200190000005A000000000000005265706169726564206279205675205175616E6720446169200000000000000000000009000000004175746F5265636F766572794A7065672000000000000000000000000000"

MCU_XY = re.compile(r"\((\d+),(\d+)\)")
SIZE_WH = re.compile(r"(\d+)x(\d+)")


# Step1. Cắt `chunk` byte sau skip_bytes ra temp_rp
def s1_cut_size(file_inp, skip_bytes, chunk=524288):
    size = os.path.getsize(file_inp)
    n = max(0, min(size - skip_bytes, chunk))
    if n:
        with open(file_inp, "rb") as f_in, open("temp_rp", "wb") as f_out:
            f_in.seek(skip_bytes)
            f_out.write(f_in.read(n))

    print(dedent(f"""
        [CUT]
         ├─ File Size  : {size:>12,} B
         ├─ Skip Offset: {skip_bytes if n else 0:>12,} B
         └─ Output Data: {n:>12,} B
    """).strip())
    return None if n else "Failed"


# Step2. JpegSize in "WxH" -> lấy W, trả 2 byte big-endian (None nếu lỗi)
def s2_get_width():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE

    out = subprocess.run(
        ["./Tool/JpegSize.exe", "temp_rp"],
        startupinfo=si, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="ignore"
    ).stdout
    os.remove("temp_rp")

    m = SIZE_WH.search(out)
    w = int(m[1]) if m else 0
    print(dedent(f"""
        [SIZE]
         └─ Width: {w or "Unknown"}
    """).strip())
    return w.to_bytes(2, "big") if 0 < w < 65536 else None


# Đã có width, giờ tìm height và 21 22 41 11
# ref: bytes thumbnail hoặc None, truyền xuống s4_created_header
def s4_replace_header(file_inp, byte_width, skip_bytes, out_dir, ref=None):
    data_fix = None
    for thu_2byte in ("21", "22", "11", "41"):
        byte_height = (320).to_bytes(2, "big")  # height mặc định 320
        data_fix, ketqua = s4_created_header(file_inp, byte_height, byte_width, thu_2byte, skip_bytes, ref)
        if ketqua == "OK":
            break

    name = os.path.basename(file_inp)
    pos = name.upper().find(".JPG")
    file_out = name[:pos] if pos != -1 else name

    os.makedirs(out_dir, exist_ok=True)
    if data_fix:
        path = os.path.join(out_dir, f"{file_out}_R.JPG")
        with open(path, "wb") as w:
            w.write(data_fix)
        return path

    print("Fail can't write data_fix (Code 111)")
    return None


def s4_created_header(file_inp, byte_height, byte_width, thu_2byte, skip_bytes, ref=None):
    text_byte = "FFD8FFDB008400010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101FFC0001108FFFFFFFF0301DD00021101031101FFC401A20000010501010101010100000000000000000102030405060708090A0B100002010303020403050504040000017D01020300041105122131410613516107227114328191A1082342B1C11552D1F02433627282090A161718191A25262728292A3435363738393A434445464748494A535455565758595A636465666768696A737475767778797A838485868788898A92939495969798999AA2A3A4A5A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9CAD2D3D4D5D6D7D8D9DAE1E2E3E4E5E6E7E8E9EAF1F2F3F4F5F6F7F8F9FA0100030101010101010101010000000000000102030405060708090A0B1100020102040403040705040400010277000102031104052131061241510761711322328108144291A1B1C109233352F0156272D10A162434E125F11718191A262728292A35363738393A434445464748494A535455565758595A636465666768696A737475767778797A82838485868788898A92939495969798999AA2A3A4A5A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9CAD2D3D4D5D6D7D8D9DAE2E3E4E5E6E7E8E9EAF2F3F4F5F6F7F8F9FAFFDA000C03010002110311003F00"

    def header(h):
        return bytes.fromhex(text_byte.replace("FFFFFFFF", (h + byte_width).hex()).replace("DD", thu_2byte))

    with open(file_inp, "rb") as f:
        body = f.read()[skip_bytes:]


    # with open("file_check.jpg", "wb") as w:
    #     w.write(header(byte_height) + body)



    height_real, ketqua = s5_decomp_jpeg(header(byte_height) + body, thu_2byte)





    if ketqua == "Failed":
        return None, "NO"

    # Header với height thật (+20 bù MCU cuối dở dang), rồi sync byte + shift MCU
    image_bytes = header((height_real + 20).to_bytes(2, "big")) + body
    data_fix, _ = jpgfix2.fix(image_bytes, ref=ref, off=None, win=256, skip=0)

    # Thêm bản quyền
    return bytes.fromhex(copyright_) + data_fix[2:], "OK"





def s5_decomp_jpeg(data, thu_2byte):
    print(f"\n[DECODE - {thu_2byte}]")
    try:
        MCUDecode.parse_jpeg(data, 0, len(data), "Log_MCUs.txt", False, True)
    except MCUDecode.JpegError as e:
        print(f" └─ {e}")
        return None, "Failed"

    MCU_PX = re.compile(r"MCU:.*?\((\d+)x(\d+)\s*pixel\)")

    def get_mcu_size():
        with open("Log_MCUs.txt", encoding="utf-8", errors="ignore") as f:
            m = MCU_PX.search(f.read(1024))
        return (int(m[1]), int(m[2])) if m else None


    size, last = get_mcu_size(), get_last_mcu_xy()
    if not size or not last:
        return None, "Failed"
    (mcu_size_x, mcu_size_y), (_, pos_mcu_h) = size, last
    height_real = mcu_size_y * pos_mcu_h



    print(f"Height: {mcu_size_y}x{pos_mcu_h} = {height_real}")
    return height_real, "SUCCESS"






# Lấy MCUs cuối cùng, ví dụ (303, 440) thì 440 * 8 = 3520 height
# 303 là MCU cuối cùng tốt, tổng mcu - 303 này = số mcu shift ?
# MCU x là: mcu tốt cuối cùng, lấy hiệu mcu ở đầu file Log_MCUs
# Là [342x48] => thì 303 - 342 = số mcu shift
def get_last_mcu_xy():
    with open("Log_MCUs.txt", "rb") as f:
        f.seek(0, 2)  # nhảy tới cuối file
        pos = f.tell()
 
        line = b""
 
        while pos > 0:
            pos -= 1
            f.seek(pos)
            char = f.read(1)
 
            if char == b"\n":
                if b"MCU" in line:
                    line = line.decode("utf-8").strip()
                    m = re.search(r"\((\d+),(\d+)\)", line)
                    if m:
                        pos_mcu_w = mcu_x = int(m.group(1))
                        pos_mcu_h = mcu_y = int(m.group(2))
 
                        return pos_mcu_w, pos_mcu_h
                line = b""
            else:
                line = char + line
 
        # dòng đầu tiên (nếu file chỉ có 1 dòng)
        if b"MCU" in line:
            line = line.decode("utf-8").strip()
            m = re.search(r"\((\d+),(\d+)\)", line)
            if m:
                pos_mcu_w = mcu_x = int(m.group(1))
                pos_mcu_h = mcu_y = int(m.group(2))
 
                return pos_mcu_w, pos_mcu_h
            # MCU x là: mcu tốt cuối cùng, lấy hiệu mcu ở đầu file Log_MCUs
            # Là [342x48] => thì 303 - 342 = số mcu shift
        # print(pos_mcu_w, pos_mcu_h)
    return None







# Trả về đường dẫn file thumbnail (dùng làm ref), None nếu không có
def check_thumbnail(p, out_dir):
    d = open(p, 'rb').read()[9:]
    m = re.search(b'\xff\xdb\x00[\x84\x43]', d)
    if not m: return None
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(p).split('.')[0]
    thumb_path = os.path.join(out_dir, base + ' (thumbnail).JPG')
    open(thumb_path, 'wb').write(bytes.fromhex(copyright_) + d[m.start():])
    open(f"{base}_v2.JPG", "wb").write(d[:m.start()])
    print('Offset thumbnail:', m.start())

    return thumb_path


def auto_color(path):
    img = Image.open(path)
    exif = img.info.get('exif')                        # giữ EXIF
    a = np.asarray(img.convert('RGB')).astype(np.float32)

    lo, hi = np.percentile(a, (1, 99), axis=(0, 1))    # kéo giãn từng kênh
    a = np.clip((a - lo) / (hi - lo + 1e-6) * 255, 0, 255)
    m = a.mean(axis=(0, 1))                            # gray-world
    a = np.clip(a * (m.mean() / m), 0, 255)

    kw = {'quality': 99}
    if exif: kw['exif'] = exif
    Image.fromarray(a.astype(np.uint8)).save(path, format='JPEG', **kw)


def _one(file_inp, out_dir, auto_color_, skip):
    print(dedent(f"""
        [SETTING]
        ├─ Part size   - {skip} B
        ├─ Get data    - 1MB/2
        ├─ 0xFFD8 SOI  - ???
        ├─ 0xFFDB DQT  - 100% 
        ├─ 0xFFC0 SOF0 - WidthAuto, HeightAuto
        ├─ 0xFFC4 DHT  - YAuto, Cb11, Cr11
        └─ 0xFFDA SOS  - ???
    """).strip())

    out_path = tmp = ref = None
    try:
        ref = check_thumbnail(file_inp, out_dir)
        if ref:
            file_inp = tmp = f"{os.path.basename(file_inp).split('.')[0]}_v2.JPG"

        if s1_cut_size(file_inp, skip) != "Failed" and (w := s2_get_width()):
            out_path = s4_replace_header(file_inp, w, skip, out_dir, ref)
            if out_path and auto_color_.upper() == 'Y':
                auto_color(out_path)

    finally:
        if tmp and os.path.exists(tmp): os.remove(tmp)
    return out_path


# folder input, output, extension, auto color, byte skip
def run(folder_path, out_dir=None, ext="jpg", auto_color_="N", byte_enc=153605, cb=lambda *a: None, st=lambda *a: None):
    out_dir = out_dir or os.path.join(folder_path, "Repaired")
    ext = ext.lower().lstrip(".")
    files = [f for f in os.listdir(folder_path) if f".{ext}" in f.lower()]
    for i, name in enumerate(files, 1):
        st(i, len(files), name)
        try:
            out = _one(f"{folder_path}/{name}", out_dir, auto_color_, byte_enc)
        except Exception as e:  # file lỗi không làm dừng cả batch
            out = None; print(f"ERR {name}: {e}")
        print("====================DONE====================")
        cb(i, len(files), name, out)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="main_cmd.py <input> [out] [auto_color Y/N] [byte_enc] [ext]")
    p.add_argument("input")
    p.add_argument("out", nargs="?")                          # mặc định: <input>/Repaired
    p.add_argument("auto_color", nargs="?", default="N")
    p.add_argument("byte_enc", nargs="?", type=int, default=153605)
    p.add_argument("ext", nargs="?", default="jpg")
    a = p.parse_args()

    # Tool/ và file tạm dùng đường dẫn tương đối => chdir về thư mục script sau khi resolve path
    inp = os.path.abspath(a.input)
    out = os.path.abspath(a.out) if a.out else None
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run(inp, out, a.ext, a.auto_color, a.byte_enc)
