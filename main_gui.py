import sys, os
import main_cmd


from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QFileDialog,
    # QListWidget,
    # QListWidgetItem,
    # QListView,
    # QAbstractItemView,
)


# /////////////////////////////////////////////
# >>> UI 💻 ──────────────────────────────────
from views.Sreen.GUI import Ui_MainWindow



class Worker(QThread):
    prog = pyqtSignal(int, int, str, str, QImage)

    def __init__(self, args, size):
        super().__init__()
        self.args, self.size = args, size

    def run(self):
        def cb(i, n, name, out):
            # scale ở thread phụ, GUI chỉ việc setPixmap
            img = QImage(out).scaled(self.size, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation) if out else QImage()
            self.prog.emit(i, n, name, out or "", img)
        main_cmd.run(*self.args, cb)



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
        u.lbl_view.setAlignment(Qt.AlignmentFlag.AlignCenter)


        # Button
        u.btn_input.clicked.connect(self.open_folder_inp)
        u.btn_output.clicked.connect(self.open_folder_out)

        u.btn_start.clicked.connect(self.start_main)



    def start_main(self):
        u = self.uic
        inp_path, out_path = u.le_input.text(), u.le_output.text()
        if not os.path.exists(inp_path): return

        u.text_log.clear(); u.pb_progress.setValue(0); u.btn_start.setEnabled(False)
        # temp file dùng chung cwd => chỉ 1 worker tại 1 thời điểm (nút Start đã khoá)
        self.w = Worker((inp_path, out_path, u.le_ext.text().lower(),
                         "Y" if u.chk_auto_color.isChecked() else "N"), u.lbl_view.size())
        self.w.prog.connect(self.on_prog)
        self.w.finished.connect(lambda: u.btn_start.setEnabled(True))
        self.w.start()


    def on_prog(self, i, n, name, out, img):
        u = self.uic
        u.text_log.append(f"{'OK...' if out else 'NO...'} {name}")
        u.pb_progress.setValue(i * 100 // n)
        if out: u.lbl_view.setPixmap(QPixmap.fromImage(img))





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
