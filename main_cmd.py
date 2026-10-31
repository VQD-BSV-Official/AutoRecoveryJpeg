import subprocess, os, sys, time, shutil, re, numpy as np
from PIL import Image
from textwrap import dedent
from pathlib import Path




# Step1. Kiểm tra file có lớn hơn 1MB không - Lấy 1MB? ......OK
def s1_cut_size(file_inp, skip_bytes):
    ONE_MB = 524288 # Nửa 1MB, 1024 * 1024  # 1MB = 1,048,576 bytes
    size = os.path.getsize(file_inp)

    if size <= skip_bytes:
        print(dedent(f"""
            [CUT]
             ├─ File Size  : {size:>12,} B
             ├─ Skip Offset: 0 B
             └─ Output Data: 0 B
        """).strip())
        return "Failed"

    with open(file_inp, "rb") as f_in, open("temp_rp", "wb") as f_out:
        # Bỏ phần đầu, VD: 153605
        f_in.seek(skip_bytes)
        remain = size - skip_bytes
        read_size = min(remain, ONE_MB) # Lấy 1Mb từ đầu mình cắt ra

        f_out.write(f_in.read(read_size))

    print()
    print(dedent(f"""
        [CUT]
         ├─ File Size  : {size:>12,} B
         ├─ Skip Offset: {skip_bytes:>12,} B
         └─ Output Data: {read_size:>12,} B
    """).strip())

# //////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////
# Step2. JpegRecovery xử lý để ra ảnh - lấy kích thức width....OK
def s2_recover_jpeg():
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE  # Ẩn cửa sổ

    pr0c = subprocess.Popen(
        ["./Tool/JpegRecovery.exe", "temp_rp"],
        startupinfo=startupinfo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,          # trả về string
        encoding="utf-8",   # tránh lỗi font
        errors="ignore"
    )

    output, _ = pr0c.communicate()   # <-- Dữ liệu mà phần mềm in ra
    # ///////////////////////////////////////////////////////// MAKUP cho đẹp
    data = {k.strip(): v.strip()
            for l in output.splitlines() if ":" in l
            for k, v in [l.split(":", 1)]}

    blocks = next((l for l in output.splitlines() if ";" in l), None)


    # ===== Safe get =====
    mcu      = data.get("MCU")
    tail     = data.get("Tail")
    fragment = data.get("Fragment Saved", "Unknown")

    # ===== Format values trước khi print =====
    block_val = ",".join(blocks.split(";")) if blocks else "Unknown"
    mcu_val  = f"{int(mcu):}" if mcu and mcu.isdigit() else "Unknown"
    tail_val = f"{int(tail):,}" if tail and tail.isdigit() else "Unknown"

    status_ = "SUCCESS" if fragment != "Unknown" else "FAILED"

    print()
    print(dedent(f"""
        [RECOVER]
         ├─ MCUs    : {mcu_val}
         ├─ Block   : {block_val}
         ├─ Tail    : {tail_val}
         ├─ Fragment: {fragment}
         └─ Status  : {status_}
    """).strip())



    time.sleep(0.25)
    if os.path.exists("temp_rp.jpg"): # Rename & delete
        os.remove("temp_rp")
    else: return "Failed"

# //////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////
# Step3 Lấy byte width, trong file temp_rp.jpg.......OK
def s3_get_byte_w():
    with open('temp_rp.jpg', 'rb') as f: # File Tốt
        data_good = f.read()

    for marker in (b"\xFF\xC0", b"\xFF\xC2"):
        pos = data_good.find(marker)
        if pos != -1: # Byte Width
            os.remove("temp_rp.jpg")
            return data_good[pos + 7:pos + 9]

    os.remove("temp_rp.jpg")

# //////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////
def s4_0_proce_header(file_inp, byte_h, byte_w, try_byte2x, skip_bytes):
    # 1. Tạo header trước đã
    data_header = s4_1_created_header(byte_h.hex(), byte_w.hex(), try_byte2x)
    # ////////////////////////////////////
    # Đọc file mã hoá bỏ qua skip_bytes
    with open(file_inp, 'rb') as f1:
        body = f1.read()[skip_bytes:]

    # Lưu Header đã tạo + phần skip_bytes file_inp
    image_bytes = data_header + body 
    with open("CHECK_.JPG", "wb") as w:
        w.write(image_bytes)

    return image_bytes

# //////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////
def auto_shift_mcu(image_bytes, count_mcu_shift):
    with open("Shift_.JPG", "wb") as w:
        w.write(image_bytes)


    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    pr0c = subprocess.Popen(['./Tool/JpegRepair.exe', 'Shift_.JPG', 'Shift_OK.JPG', 'insert', f'{count_mcu_shift}'], startupinfo=startupinfo)
    pr0c.communicate(); print('heloooooooooooooooooooooooooooooooooooooooooo')

    time.sleep(1)
    with open("Shift_ok.JPG", "rb") as r:
        data_out = r.read()

    os.remove("Shift_.JPG"); os.remove("Shift_ok.JPG")
    return data_out



def s4_replace_header(file_inp, byte_w, skip_bytes, out_dir):

    byte2x = ["21", "22", "11", "41"]
    image_bytes = None
    for try_byte2x in byte2x:
        # ////////////////////////////////////
        byte_h = (320).to_bytes(2, 'big')
        s4_0_proce_header(file_inp, byte_h, byte_w, try_byte2x, skip_bytes)
        height_real, mcu_size_x, count_mcu_shift, ketqua = s4_2_decomp_jpeg(try_byte2x)

        # Nếu kết quả decode được thì thoát
        if ketqua != "Failed":
            byte_h = (height_real).to_bytes(2, 'big')
            image_bytes = s4_0_proce_header(file_inp, byte_h, byte_w, try_byte2x, skip_bytes)
            image_bytes = auto_shift_mcu(image_bytes, count_mcu_shift)
            
            break

        else: continue

    os.remove("CHECK_.JPG")

    # ////////////////////////////////////
    name = os.path.basename(file_inp)
    pos = name.upper().find(".JPG")
    file_out = name[:pos] if pos != -1 else name

    os.makedirs(out_dir, exist_ok=True) # Lưu ra folder Repaired
    if image_bytes:
        with open(os.path.join(out_dir, f"{file_out}_R.JPG"), "wb") as w:
            w.write(image_bytes)

            return os.path.join(out_dir, f"{file_out}_R.JPG") # trả về đường dẫn file đã lưu

    print("Fail can't write image_bytes (Code 168)")
    return None


# //////////////////////////////////////////////////////////
def s4_1_created_header(byte_h, byte_w, byte2x):
    text_byte = "FFD8FFDB008400010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101FFC0001108FFFFFFFF0301DD00021101031101FFC401A20000010501010101010100000000000000000102030405060708090A0B100002010303020403050504040000017D01020300041105122131410613516107227114328191A1082342B1C11552D1F02433627282090A161718191A25262728292A3435363738393A434445464748494A535455565758595A636465666768696A737475767778797A838485868788898A92939495969798999AA2A3A4A5A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9CAD2D3D4D5D6D7D8D9DAE1E2E3E4E5E6E7E8E9EAF1F2F3F4F5F6F7F8F9FA0100030101010101010101010000000000000102030405060708090A0B1100020102040403040705040400010277000102031104052131061241510761711322328108144291A1B1C109233352F0156272D10A162434E125F11718191A262728292A35363738393A434445464748494A535455565758595A636465666768696A737475767778797A82838485868788898A92939495969798999AA2A3A4A5A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9CAD2D3D4D5D6D7D8D9DAE2E3E4E5E6E7E8E9EAF2F3F4F5F6F7F8F9FAFFDA000C03010002110311003F00"

    text_byte = text_byte.replace("FFFFFFFF", byte_h + byte_w)
    text_byte = text_byte.replace("DD", byte2x) # Kênh 

    data_out = bytes.fromhex(text_byte)
    return data_out


def s4_2_decomp_jpeg(try_byte2x):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    pr0c = subprocess.Popen(
        ['./Tool/JpegDecomp.exe', '-decode', '-fin', 'CHECK_.JPG', '-fout', "Log_MCUs.txt"],
        startupinfo=startupinfo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,          # trả về string
        encoding="utf-8",   # tránh lỗi font
        errors="ignore"
    )

    output, _ = pr0c.communicate()   # <-- lấy dữ liệu ở đây
    # ///////////////////////////////////////////////////////// MAKUP cho đẹp
    val_mcu = re.search(r"\[(\d+)x(\d+)", output); val_size = re.search(r"\((\d+)x(\d+)\s*pixel\)", output)
    if val_mcu and val_size:
        mcu_w, mcu_h = int(val_mcu.group(1)), int(val_mcu.group(2))
        mcu_size_x, mcu_size_y = int(val_size.group(1)), int(val_size.group(2))
        # print(mcu_w)


    print(f"\n[DECODE - {try_byte2x}]")
    lines = output.splitlines()

    for i, line in enumerate(lines):
        prefix = " └─ " if i == len(lines) - 1 else " ├─ "
        print(prefix + line)



    if os.path.exists("Log_MCUs.txt"):
        count_error = 0
        with open("Log_MCUs.txt", "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= 5000:
                    break
                count_error += line.count("Huffman error")

        # print("Số chữ 'Huffman':", count_error)
        height_real = None
        if count_error <= 3:

            pos_mcu_w, pos_mcu_h = get_last_mcu_xy()
            height_real = mcu_size_y * pos_mcu_h # Kích thước ảnh height
            count_mcu_shift = mcu_w - pos_mcu_w # Số mcu để shift

            print(f"Height: {mcu_size_y}x{pos_mcu_h} = {height_real}")
            print(f"MCUs Shift: {mcu_w}-{pos_mcu_w} = {count_mcu_shift}")

            return height_real, mcu_size_x, count_mcu_shift, "SUCCESS"
        else:
            return height_real, None, None, "Failed"

# //////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////
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
                if b"/// MCU" in line:
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
        if b"/// MCU" in line:
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



def auto_color(path):
    img = Image.open(path)
    exif = img.info.get('exif')                        # giữ EXIF (ngày chụp, hướng ảnh...)
    a = np.asarray(img.convert('RGB')).astype(np.float32)

    lo, hi = np.percentile(a, (1, 99), axis=(0, 1))    # kéo giãn từng kênh
    a = np.clip((a - lo) / (hi - lo + 1e-6) * 255, 0, 255)
    m = a.mean(axis=(0, 1))                            # gray-world
    a = np.clip(a * (m.mean() / m), 0, 255)

    kw = {'quality': 95}
    if exif: kw['exif'] = exif
    Image.fromarray(a.astype(np.uint8)).save(path, format='JPEG',**kw)




def check_thumbnail(p, out_dir):
    d=open(p,'rb').read()
    m=re.compile(b'\xff\xd8\xff[\xe0\xe1]').search(d, 153605)
    if m:
        os.makedirs(out_dir,exist_ok=True)
        open(os.path.join(out_dir,os.path.basename(p).split('.')[0]+' (thumbnail).JPG'),'wb').write(d[m.start():]);print('Offset thumbnail:',m.start())



# ////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
# ////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
def run(folder_path, out_dir, ext, auto_color_):
    for file_name in os.listdir(folder_path):
        if f".{ext}" in file_name.lower():

            file_inp = f"{folder_path}/{file_name}"
            

            # //////////////////////////////////////////////////////////
            print(dedent(f"""
                [SETTING]
                ├─ Part size   - 153605 B
                ├─ Get data    - 1MB/2
                ├─ 0xFFD8 SOI  - ???
                ├─ 0xFFDB DQT  - 100% 
                ├─ 0xFFC0 SOF0 - WidthAuto, HeightAuto
                ├─ 0xFFC4 DHT  - YAuto, Cb11, Cr11
                └─ 0xFFDA SOS  - ???
            """).strip())

            check_thumbnail(file_inp, out_dir) # 0. Kiểm tra có thumbnail không
            # 1. Kiểm tra file có lớn hơn 1MB không - Lấy 1MB?
            result = s1_cut_size(file_inp, skip_bytes = 153605)

            if result != "Failed": # (Là thành công)
                # Dùng JpegRecovery để khôi phục lấy width
                result2 = s2_recover_jpeg()


                if result2 != "Failed": # (Là thành công)
                    byte_w = s3_get_byte_w() # Lấy width và thay header
                    out_path = s4_replace_header(file_inp, byte_w, 153605, out_dir)

                    if auto_color_.upper() == 'Y': # Tự chỉnh màu (Ghi đè file)
                        auto_color(out_path)

            print("====================DONE====================")








# if len(sys.argv) < 4:
#     print("Usage: main_cmd.py <path_inp> <path_out> <extension> [Y/N auto color]")
#     sys.exit(1)

# folder_path = sys.argv[1]
# out_dir     = sys.argv[2]
# ext         = sys.argv[3]

# auto_color_ = sys.argv[4] if len(sys.argv) > 4 else "N"