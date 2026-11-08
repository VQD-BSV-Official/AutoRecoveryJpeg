## ***Developed by Vũ Quang Đại / <QDai.Dev08@gmail.com>***
 > [!IMPORTANT]
> This software was built by me, at no cost and without outside support. Please respect the effort behind it.
> **Do not copy the code and pass it off as your own**, e.g. by replacing **Author & Developed by: Vu Quang Dai** with your name. It is open source and you may modify it, but credit is required.

- ✍️ ~99% hand-written; the remaining ~1% used AI only to simplify the code
- ⏱️ Many mistakes and countless hours went into getting it right
- 🎯 Planned to continue up to **v2.0.0**, then stop (limited funding, research is time-consuming)

***If this project helps you, consider donating:***

<p align="center">
  <img src="https://github.com/user-attachments/assets/e44b917f-01d5-4df4-af07-17703e9b553f" alt="BIDV QR" width="200">
  <img src="https://github.com/user-attachments/assets/4dbc8764-1209-4233-87db-5cf78c9e8cf7" alt="PayPal Image" width="230"/> <br><br>

  <a href="https://www.paypal.com/paypalme/QDaiDev08">
    <img src="https://img.shields.io/badge/Donate-PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white" alt="PayPal">
  </a>
</p>

[![Python](https://img.shields.io/badge/Python-3.x-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-538CC9?style=for-the-badge&logo=qt&logoColor=white)](https://www.riverbankcomputing.com/software/pyqt/intro)
[![Qt Designer](https://img.shields.io/badge/UI%20Design-Qt%20Designer-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://doc.qt.io/qt-6/qtdesigner-manual.html)
[![Pillow](https://img.shields.io/badge/Imaging-Pillow-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python-pillow.org/) <br>

---


## 📚 References & Acknowledgements

This project was developed through independent research and learning from the following resources:

- [RecoverJpeg](https://github.com/VQD-BSV-Official/RecoveryJpeg)
- [JpegRepair](https://github.com/dmahurin/jpegrepair)
- [JpegRecovery](https://github.com/euzun/jpeg-carver-csharp)
- [JpegDecomp](https://github.com/albmac/jpeg-decomp)
- [JPEGVisualRepairTool](https://github.com/albmac/JPEGVisualRepairTool)
- [JpegRepairShop](https://anderspedersen.net/jpegrepair/)
- [Wikipedia – JPEG](https://en.wikipedia.org/wiki/JPEG)
* E. Uzun and H. T. Sencar, “[JpgScraper: An Advanced Carver for JPEG Files](https://doi.org/10.1109/TIFS.2019.2953382)”, IEEETransactions on Information Forensics and Security, 2019.
* E. Uzun and H. T. Sencar, “[Carving orphaned jpeg file fragments](https://www.researchgate.net/publication/275044127_Carving_Orphaned_JPEG_File_Fragments)”, IEEETransactions on Information Forensics and Security, vol. 10, no. 8, pp.1549–1563, 2015.

Thanks to the developers and communities behind these resources.

---

## 🚀 Running the Application
> [!Note]
> Run from the folder that contains `Tool/` (`JpegRecovery.exe`). The CLI switches to the script folder automatically; the module and the GUI do not.

- **Libs**
```
pip install PyQt6 pillow numpy
```



- **Run CLI**
```bash
python main_cmd.py <input> [out] [auto_color Y/N] [byte_enc] [ext]
```
| Argument     | Default            | Description                                   |
|--------------|--------------------|-----------------------------------------------|
| `input`      | *(required)*       | Folder containing the encrypted files         |
| `out`        | `<input>/Repaired` | Folder for repaired JPEG files                |
| `auto_color` | `N`                | `Y` = auto-adjust colors, `N` = off           |
| `byte_enc`   | `153605`           | Size of the encrypted part in bytes           |
| `ext`        | `jpg`              | Extension of the encrypted files              |


Arguments are positional: to set `byte_enc`, pass `out` and `auto_color` first.


```bash
# Defaults (Recomen)
python main_cmd.py "D:/Data/Ransom"
 
# Custom output, auto color, encrypted part size
python main_cmd.py "D:/Data/Ransom" "D:/Data/Fixed" Y 153605
```
 
- **Use as a module**
```python
import main_cmd
 
main_cmd.run(r"D:/Data/Ransom", out_dir=None, ext="jpg", auto_color_="N", byte_enc=153605)
```



- **Build Executable**
```bash
pyinstaller --noconfirm --onefile --windowed --icon "resources/logo.ico" --name "AutoRecoveryJpeg" --clean --log-level "INFO" --noupx --version-file "VERSION" "main_gui.py"
```


## Usage - 🎬 Demo Video
<a href="https://youtu.be/shIFJ2Jxqns">
  <img src="https://img.youtube.com/vi/shIFJ2Jxqns/maxresdefault.jpg" width="600">
</a>
