# from PIL import Image
# import numpy as np

# def find(image_path, threshold=2):
#     # img = Image.open(image_path).convert("L")  # grayscale
#     # arr = np.array(img)
#     with Image.open(image_path) as img:
#         img = img.convert("L")
#         arr = np.array(img)

#     h, w = arr.shape

#     gray_start = h

#     # Duyệt từ dưới lên
#     for y in range(h-1, -1, -1):
#         line = arr[y]
#         var = np.var(line)

#         if var > threshold:  # có chi tiết → ảnh thật
#             gray_start = y + 1
#             break

#     # print("Image height:", h)
#     # print("Gray area starts at line:", gray_start - 16)
#     # print("Gray lines:", h - gray_start)
#     gray_start = h - gray_start

#     if gray_start <= 0: gray_start = None
#     return gray_start

# # gray_line = find_gray_start("a.JPG")




from PIL import Image
import numpy as np
import io

def auto_find_gray(image_bytes, threshold=2):
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("L")
        arr = np.array(img)

    h, w = arr.shape

    gray_start = h

    for y in range(h-1, -1, -1):
        line = arr[y]
        var = np.var(line)

        if var > threshold:
            gray_start = y + 1
            break

    gray_start = h - gray_start
    if gray_start <= 0:
        gray_start = None

    return gray_start



from PIL import Image
import numpy as np
import io

def auto_shift_mcu_from_bytes(image_bytes, mcu_count, mcu_width=16):
    # đọc ảnh từ bytes
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    data = np.array(img)

    shift_px = mcu_count * mcu_width

    # shift vòng (wrap)
    shifted = np.hstack((data[:, -shift_px:], data[:, :-shift_px]))

    # trả về bytes thay vì save file
    out_io = io.BytesIO()
    Image.fromarray(shifted).save(out_io, format="JPEG", quality=99)

    return out_io.getvalue()


# shift_mcu_right("20151128_144500_R.JPG", "out.jpg", 29, 16)
