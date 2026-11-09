import sys, os
import main_cmd


from PyQt6.QtCore import QThread, pyqtSignal, Qt, QSize
from PyQt6.QtGui import QImage, QPixmap, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QFileDialog,
    QListWidgetItem,
)


# /////////////////////////////////////////////
# >>> UI 💻 ──────────────────────────────────
from views.Sreen.GUI import Ui_MainWindow

THUMB = QSize(256, 256)  # khớp iconSize trong .ui; ~256KB RAM/ảnh


class Worker(QThread):
    prog = pyqtSignal(int, int, str, str, QImage)
    begin = pyqtSignal(int, int, str)

    def __init__(self, args):
        super().__init__()
        self.args = args

    def run(self):
        def cb(i, n, name, out):
            # scale ở thread phụ, GUI chỉ việc tạo icon
            img = QImage(out).scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation) if out else QImage()
            self.prog.emit(i, n, name, out or "", img)
        main_cmd.run(*self.args, cb, self.begin.emit)



class MainWindow:
    def __init__(self):
        self.main_win = QMainWindow()
        self.uic = Ui_MainWindow()
        self.uic.setupUi(self.main_win)


        self._init_signals()

        

    def _init_signals(self):
        u = self.uic

        # Setting
        u.le_input.setPlaceholderText("Folder with encrypted file")
        u.le_output.setPlaceholderText("Folder for repaired JPEG file")

        # Button
        u.btn_input.clicked.connect(self.open_folder_inp)
        u.btn_output.clicked.connect(self.open_folder_out)

        u.btn_start.clicked.connect(self.start_main)



    def start_main(self):
        u = self.uic
        inp_path, out_path = u.le_input.text(), u.le_output.text()
        if not os.path.exists(inp_path): return

        u.text_log.clear(); u.btn_start.setEnabled(False)
        u.lst_view.clear()
        u.pb_progress.setRange(0, 0)  # busy cho tới khi biết tổng số file
        # temp file dùng chung cwd => chỉ 1 worker tại 1 thời điểm (nút Start đã khoá)
        self.w = Worker((inp_path, out_path, u.le_ext.text().lower(),
                         "Y" if u.chk_auto_color.isChecked() else "N",
                         int(u.le_size.text()) if u.le_size.text().isdigit() else 153605))
        u.text_log.append("START")
        self.w.begin.connect(self.on_begin)
        self.w.prog.connect(self.on_prog)
        self.w.finished.connect(self.on_done)
        self.w.start()


    def on_begin(self, i, n, name):
        u = self.uic
        if i == 1: u.pb_progress.setRange(0, n); u.pb_progress.setValue(0)
        # u.text_log.append(f"... {name}")

    def on_done(self):
        u = self.uic
        if u.pb_progress.maximum() == 0:  # không có file nào
            u.pb_progress.setRange(0, 1); u.text_log.append("No file")
        u.btn_start.setEnabled(True)

    def on_prog(self, i, n, name, out, img):
        u = self.uic
        u.text_log.append(f"{'OK...' if out else 'NO...'} {name}")
        u.pb_progress.setValue(i)
        if not out: return
        lst = u.lst_view
        sb = lst.horizontalScrollBar()
        follow = sb.value() == sb.maximum()  # đang ở cuối thì tự bám ảnh mới
        it = QListWidgetItem(QIcon(QPixmap.fromImage(img)), "")
        it.setToolTip(name)
        lst.addItem(it)
        if follow: lst.scrollToItem(it)





# ════════════════════════════════════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════════════════════════════════════
    # Open Folder - OK
    def open_folder_inp(self):
        self.folder_inp = QFileDialog.getExistingDirectory(None, "Select Folder")

        if self.folder_inp:
            self.uic.le_input.setText(self.folder_inp)
            self.uic.le_output.setText(self.folder_inp + "/Repaired")



    def open_folder_out(self):
        self.folder_out = QFileDialog.getExistingDirectory(None, "Select Folder")
        if self.folder_out:
            self.uic.le_output.setText(self.folder_out)

# ════════════════════════════════════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════════════════════════════════════



    def show(self):
        # command to run
        self.main_win.show()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec())
