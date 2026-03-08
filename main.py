import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtCore import Qt

from ui.main_window import MainWindow


def apply_dark_theme(app: QApplication):
    app.setStyle("Fusion")
    palette = QPalette()

    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Button, QColor(40, 40, 40))
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 215))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)

    app.setPalette(palette)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_theme(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())