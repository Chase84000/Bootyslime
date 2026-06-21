#!/usr/bin/env python3
import sys
import time
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QScreen
import finance_lens_qt

def main():
    app = QApplication(sys.argv)
    # Create but don't show yet to avoid flicker in some envs
    window = finance_lens_qt.FinanceLensApp()
    window.show()
    window.show_view("dashboard")
    window.raise_()
    window.activateWindow()

    def grab_and_save():
        window.show_view("dashboard")
        window.raise_()
        window.activateWindow()
        QApplication.processEvents()
        # Grab specifically this window
        pix = window.grab()
        out = r"C:\Users\nouve.DESKTOP-IDVQJ79\dashboard_after_fixes.png"
        pix.save(out, "PNG")
        print(f"Saved clean dashboard screenshot to {out}")
        window.close()
        app.quit()

    QTimer.singleShot(1800, grab_and_save)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
