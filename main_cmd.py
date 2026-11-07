import subprocess, os, time, re, numpy as np
from PIL import Image
from textwrap import dedent
from controllers import jpgfix2, MCUDecode

copyright_ = "FFD8FFE1007C45786966000049492A000800000003000E010200270000003200000012010300010000000100000031010200190000005A000000000000005265706169726564206279205675205175616E6720446169200000000000000000000009000000004175746F5265636F766572794A7065672000000000000000000000000000"

MCU_XY = re.compile(r"\((\d+),(\d+)\)")


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


# Step2. JpegRecovery xử lý để ra ảnh - lấy width
def s2_recover_jpeg():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE

    out = subprocess.run(
        ["./Tool/JpegRecovery.exe", "temp_rp"],
        startupinfo=si, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="ignore"
    ).stdout

    lines = out.splitlines()
    data = {k.strip(): v.strip() for l in lines if ":" in l for k, v in [l.split(":", 1)]}
    blocks = next((l for l in lines if ";" in l), None)

    num = lambda k, fmt: format(int(data[k]), fmt) if data.get(k, "").isdigit() else "Unknown"
    frag = data.get("Fragment Saved", "Unknown")

    print(dedent(f"""
        [RECOVER]
         ├─ MCUs    : {num("MCU", "")}
         ├─ Block   : {blocks.replace(";", ",") if blocks else "Unknown"}
         ├─ Tail    : {num("Tail", ",")}
         ├─ Fragment: {frag}
         └─ Status  : {"SUCCESS" if frag != "Unknown" else "FAILED"}
    """).strip())

    time.sleep(0.25)
    if not os.path.exists("temp_rp.jpg"):
        return "Failed"

    os.remove("temp_rp")


# Step3. Lấy byte width trong temp_rp.jpg
def s3_get_byte_w():
    with open("temp_rp.jpg", "rb") as f:
        d = f.read()
    os.remove("temp_rp.jpg")

    for m in (b"\xFF\xC0", b"\xFF\xC2"):
        if (p := d.find(m)) != -1:
            return d[p + 7:p + 9]


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

    # with open("File_test.jpg", "wb") as w:
    #     w.write(header(byte_height) + body)

    height_real, ketqua = s5_decomp_jpeg(header(byte_height) + body, thu_2byte)
    if ketqua == "Failed":
        return None, "NO"

    # Header với height thật (+20 bù MCU cuối dở dang), rồi sync byte + shift MCU
    image_bytes = header((height_real + 20).to_bytes(2, "big")) + body
    data_fix, _ = jpgfix2.fix(image_bytes, ref=ref, off=None, win=256, skip=0)

    # Thêm bản quyền
    return bytes.fromhex(copyright_) + data_fix[2:], "OK"


# Decode in-process bằng MCUDecode --adv (decode tới FFD9, bỏ qua tổng MCU trong header)
def s5_decomp_jpeg(data, thu_2byte):
    print(f"\n[DECODE - {thu_2byte}]")
    try:
        MCUDecode.parse_jpeg(data, 0, len(data), "Log_MCUs.txt", False, True)
    except MCUDecode.JpegError as e:
        print(f" └─ {e}")
        return None, "Failed"

    with open("Log_MCUs.txt", encoding="utf-8", errors="ignore") as f:
        txt = f.read()

    head = txt.split("\n\n", 1)[0].splitlines()
    for i, l in enumerate(head):
        print((" └─ " if i == len(head) - 1 else " ├─ ") + l)

    size = re.search(r"\((\d+)x(\d+)\s*pixel\)", txt)
    lines = txt.split("\n", 5000)[:5000]
    if not size or sum("Huffman error" in l for l in lines) > 3:
        return None, "Failed"

    i = txt.rfind("\nMCU ")  # MCU cuối cùng đã decode
    m = MCU_XY.search(txt, i) if i >= 0 else None
    if not m:
        return None, "Failed"

    height_real = int(size.group(2)) * int(m.group(2))
    print(f"Height: {size.group(2)}x{m.group(2)} = {height_real}")
    return height_real, "SUCCESS"


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

        if s1_cut_size(file_inp, skip) != "Failed" and s2_recover_jpeg() != "Failed":
            out_path = s4_replace_header(file_inp, s3_get_byte_w(), skip, out_dir, ref)
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
