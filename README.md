## ***Developed by Vũ Quang Đại / <QDai.Dev08@gmail.com>***
> [!Important]
> Please respect copyright, I created this software without any cost or support. ***DO NOT STEAL THE CODE AND MAKE IT YOURS, WHILE EVERYTHING IS BUILT BY ME***. For example: edit ***Author & Developed by: Vu Quang Dai*** to your name, I know open source you can edit everything, but I want respect for the effort I put in. **You can run the code directly using Python to update to the latest versions instead of waiting for the .exe release. Install any missing libraries, and if you encounter errors, please fix them and contribute your suggestions.** I coded 99% by hand, and the remaining 1% was done using AI to simplify the code. It took many mistakes to get the code right, and I spent hours at the computer. I didn't use AI for coding, so I cherish everything I've done. Therefore, you can donate to me.
> * ```You can contact me via gmail (email) to save the files you need. This will cost you a fee, thank you. If you have any problems or questions, I will reply as soon as possible. I will probably develop to version 2.0.0 and then stop. Lack of funding is getting me down and research is time consuming.```
> * <img src="https://github.com/user-attachments/assets/e44b917f-01d5-4df4-af07-17703e9b553f" alt="BIDV" width="200" style="max-width: 100%; margin: 0 auto;"> <br>
> * **Donate (Paypal)**: [Do consider donating or buying us a coffee](https://www.paypal.com/paypalme/QDaiDev08)


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
> Run from the folder that contains `Tool/` (`JpegRecovery.exe`, `JpegRepair.exe`, `JpegDecomp.exe`). The CLI switches to the script folder automatically; the module and the GUI do not.

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
