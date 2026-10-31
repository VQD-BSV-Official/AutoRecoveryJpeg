import sys


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
        inp_path = self.uic.le_input.text()
        out_path = self.uic.le_output.text()

        ext = self.uic.le_ext.text()
        byte_ = self.uic.le_size.text()


        if self.uic.chk_auto_color.isChecked():
            auto_color_ = "Y"

        if os.path.exists(inp_path):
            self.main_cmd.run(inp_path, out_path, ext, auto_color_)





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