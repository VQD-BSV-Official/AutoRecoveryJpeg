# 1. Kiểm tra file có lớn hơn 1MB không - Lấy 1MB?
def resize_file(input_file, skip_bytes):
    ONE_MB = 1024 * 1024  # 1MB = 1,048,576 bytes
    size = os.path.getsize(input_file)

    if size <= skip_bytes:
        print(dedent(f"""
            [CUT]
             ├─ File Size  : {size:>12,} B
             ├─ Skip Offset: 0 B
             └─ Output Data: 0 B
        """).strip())
        return "Failed"

    with open(input_file, "rb") as f_in, open("temp_rp", "wb") as f_out:
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
# 2. JpegRecovery xử lý để ra ảnh - lấy kích thức width
def proce_jpeg():
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


def decomp_jpeg():
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    # pr0c = subprocess.Popen(['./Tool/JpegDecomp.exe', '-decode', '-fin', 'CHECK_SAMPLING_.JPG', '-fout', "Log_MCUs.txt"], startupinfo=startupinfo)
    # pr0c.wait()

    pr0c = subprocess.Popen(
        ['./Tool/JpegDecomp.exe', '-decode', '-fin', 'CHECK_SAMPLING_.JPG', '-fout', "Log_MCUs.txt"],
        startupinfo=startupinfo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,          # trả về string
        encoding="utf-8",   # tránh lỗi font
        errors="ignore"
    )

    output, _ = pr0c.communicate()   # <-- lấy dữ liệu ở đây
    # ///////////////////////////////////////////////////////// MAKUP cho đẹp
    print("\n[DECODE]")
    lines = output.splitlines()

    for i, line in enumerate(lines):
        prefix = " └─ " if i == len(lines) - 1 else " ├─ "
        print(prefix + line)



    if os.path.exists("Log_MCUs.txt"):
        count_error = 0
        with open("Log_MCUs.txt", "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= 20000:
                    break
                count_error += line.count("Huffman error")

        # print("Số chữ 'Huffman':", count_error)
        if count_error >= 4: # Do mặc định là 21 rồi thì ngược lại sẽ là 22
            os.remove("Log_MCUs.txt")
            return "22"


# //////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////
def get_width_and_byte2x():
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
def replace_header(file_inp, width, skip_bytes):
    count_height = 320
    byte2x = "21"
    while True:
        height = count_height.to_bytes(2, 'big') # Tạo header
        pixels = height.hex() + width.hex()

        if count_height >= 4000: count_height += 32
        else: count_height += 64
        # print(count_height)

        text_byte = "FFD8FFDB008400010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101FFC0001108FFFFFFFF0301DD00021101031101FFC401A20000010501010101010100000000000000000102030405060708090A0B100002010303020403050504040000017D01020300041105122131410613516107227114328191A1082342B1C11552D1F02433627282090A161718191A25262728292A3435363738393A434445464748494A535455565758595A636465666768696A737475767778797A838485868788898A92939495969798999AA2A3A4A5A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9CAD2D3D4D5D6D7D8D9DAE1E2E3E4E5E6E7E8E9EAF1F2F3F4F5F6F7F8F9FA0100030101010101010101010000000000000102030405060708090A0B1100020102040403040705040400010277000102031104052131061241510761711322328108144291A1B1C109233352F0156272D10A162434E125F11718191A262728292A35363738393A434445464748494A535455565758595A636465666768696A737475767778797A82838485868788898A92939495969798999AA2A3A4A5A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9CAD2D3D4D5D6D7D8D9DAE2E3E4E5E6E7E8E9EAF2F3F4F5F6F7F8F9FAFFDA000C03010002110311003F00"

        text_byte = text_byte.replace("FFFFFFFF", pixels)
        text_byte = text_byte.replace("DD", byte2x) # Kênh 

        data_out = bytes.fromhex(text_byte)
        # with open("File_out.jpg", "wb") as w:
        #     w.write(data_out)

        # with open(file_inp, 'rb') as f1, open('File_out.jpg', 'ab') as f2:
        #     f2.write(f1.read()[153605:None])
        
        with open(file_inp, 'rb') as f1:
            body = f1.read()[skip_bytes:]

        image_bytes = data_out + body
        if count_height == 448:
            with open("CHECK_SAMPLING_.JPG", "wb") as w:
                w.write(image_bytes)

            if decomp_jpeg() == "22": byte2x = "22"
            os.remove("CHECK_SAMPLING_.JPG")

        Ket_Qua = Dectect_.auto_find_gray(image_bytes)
        pos = file_inp.upper().find(".JPG")
        file_out = file_inp[:pos] if pos != -1 else file_inp
        if Ket_Qua: 
            with open(f"{file_out}_R.JPG", "wb") as w:
                w.write(image_bytes)
            break


import subprocess, os, sys, time, shutil
from textwrap import dedent
from pathlib import Path
from DEF import Dectect_

file_inp = input('\nFile Encrypted: ')
# file_inp = 'IMG_1230.JPG.isza'
# //////////////////////////////////////////////////////////
print(dedent(f"""
    [SETTING]
     ├─ 0xFFD8 SOI  - ???
     ├─ 0xFFDB DQT  - 100% 
     ├─ 0xFFC0 SOF0 - WidthAuto, HeightAuto
     ├─ 0xFFC4 DHT  - YAuto, Cb11, Cr11
     └─ 0xFFDA SOS  - ???
""").strip())



# 1. Kiểm tra file có lớn hơn 1MB không - Lấy 1MB?
result = resize_file(file_inp, skip_bytes = 153605)

if result != "Failed": result2 = proce_jpeg()
if result2 != "Failed": 
    width = get_width_and_byte2x()
    replace_header(file_inp, width, skip_bytes = 153605)


