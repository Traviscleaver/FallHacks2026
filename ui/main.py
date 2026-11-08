# main.py — Standardized Shell with Smooth Color-Crossfade Theme Animation

import sys
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, Slot, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFileDialog
)
from theme import MODERN_DARK_GREY_QSS, MODERN_LIGHT_QSS
from gemini_stub import GeminiServiceStub
from workspace_view import WorkspaceView, bind_debounced
from drilldown_view import DrillDownView
from slide_deep_dive import DeepDiveService


class LectureBuddyApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lecture Buddy")
        self.resize(1280, 800)

        self.gemini = GeminiServiceStub()
        self.is_recording = False
        self.is_finishing = False
        self.is_light_mode = False
        self.seconds = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("TopHeader")
        header.setFixedHeight(60)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)
        header_layout.setSpacing(10)

        greeting_col = QVBoxLayout()
        greeting_col.setContentsMargins(0, 10, 0, 10)
        greeting_col.setSpacing(2)

        lbl_date = QLabel("LIVE WORKSPACE")
        lbl_date.setObjectName("WorkspaceBadge")

        lbl_hello = QLabel("Lecture Buddy")
        lbl_hello.setObjectName("AppTitle")

        greeting_col.addWidget(lbl_date)
        greeting_col.addWidget(lbl_hello)
        header_layout.addLayout(greeting_col)
        header_layout.addStretch()

        self.btn_theme = QPushButton("☀ Light Mode")
        self.btn_theme.setObjectName("SubtleActionButton")
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        bind_debounced(self.btn_theme, self.toggle_theme)

        self.btn_record = QPushButton(f"● Start Recording ({self.fmt(self.seconds)})")
        self.btn_record.setObjectName("RecordButton")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        bind_debounced(self.btn_record, self.toggle_recording)

        self.btn_export = QPushButton("Export PDF")
        self.btn_export.setObjectName("AccentButton")
        self.btn_export.setCursor(Qt.PointingHandCursor)
        bind_debounced(self.btn_export, self.on_export_pdf)

        header_layout.addWidget(self.btn_theme)
        header_layout.addWidget(self.btn_record)
        header_layout.addWidget(self.btn_export)

        root_layout.addWidget(header)

        self.stack = QStackedWidget()
        self.workspace = WorkspaceView(self.gemini)
        self.deep_dive = DeepDiveService(api_key=self.gemini.api_key, parent=self)
        self.drilldown = DrillDownView(self.deep_dive)

        self.stack.addWidget(self.workspace)
        self.stack.addWidget(self.drilldown)
        root_layout.addWidget(self.stack, stretch=1)

        self.workspace.openDrillDown.connect(self.open_detail)
        self.workspace.openSlideDeepDive.connect(self.open_slide)
        self.drilldown.backRequested.connect(lambda: self.stack.setCurrentWidget(self.workspace))
        self.gemini.recording_status.connect(self.on_recording_status)

        # Initial stylesheet setup
        self.setStyleSheet(MODERN_DARK_GREY_QSS)

    def toggle_theme(self):
        self.is_light_mode = not self.is_light_mode
        if self.is_light_mode:
            self.btn_theme.setText("☾ Dark Mode")
            target_bg = QColor("#F4F4F5")
            start_bg = QColor("#121212")
        else:
            self.btn_theme.setText("☀ Light Mode")
            target_bg = QColor("#121212")
            start_bg = QColor("#F4F4F5")

        # Smooth color cross-fade animation (250ms)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(250)
        self.anim.setStartValue(start_bg)
        self.anim.setEndValue(target_bg)
        self.anim.valueChanged.connect(self.apply_animated_bg)
        self.anim.start()

        self.workspace.set_theme_mode(self.is_light_mode)
        self.drilldown.set_theme_mode(self.is_light_mode)

    @Slot(QColor)
    def apply_animated_bg(self, color: QColor):
        # Dynamically interpolate base window background during transition
        base_stylesheet = MODERN_LIGHT_QSS if self.is_light_mode else MODERN_DARK_GREY_QSS
        transition_qss = base_stylesheet + f"\nQMainWindow, QWidget {{ background-color: {color.name()}; }}"
        self.setStyleSheet(transition_qss)
        if self.anim.state() == QVariantAnimation.Stopped:
            self.setStyleSheet(base_stylesheet)

    def debounced_connect(self, button: QPushButton, slot):
        bind_debounced(button, slot)

    def toggle_recording(self):
        if self.is_finishing:
            return  # waiting for the last finals; "stopped" clears this
        if not self.is_recording:
            if not self.gemini.start_recording():
                return  # error already shown in the transcript panel
            self.is_recording = True
            self.timer.start(1000)
            self.btn_record.setText(f"■ Stop Recording ({self.fmt(self.seconds)})")
        else:
            self.is_recording = False
            self.timer.stop()
            self.gemini.stop_recording()
            self.is_finishing = True
            self.btn_record.setText(f"… Finishing ({self.fmt(self.seconds)})")

    @Slot(str)
    def on_recording_status(self, status: str):
        if status == "stopped":
            self.is_finishing = False
            self.btn_record.setText(f"● Start Recording ({self.fmt(self.seconds)})")
        elif status == "error" and self.is_recording:
            self.is_recording = False
            self.timer.stop()
            self.btn_record.setText(f"● Start Recording ({self.fmt(self.seconds)})")

    def closeEvent(self, event):
        self.gemini.shutdown()
        self.deep_dive.shutdown()
        super().closeEvent(event)

    def tick(self):
        self.seconds += 1
        self.btn_record.setText(f"■ Stop Recording ({self.fmt(self.seconds)})")

    @staticmethod
    def fmt(sec: int) -> str:
        m, s = divmod(sec, 60)
        return f"{m:02d}:{s:02d}"

    def open_detail(self, title: str, badge: str, body: str):
        self.drilldown.load_item(title, badge, body, self.workspace.transcript_text)
        self.stack.setCurrentWidget(self.drilldown)

    def open_slide(self, ctx):
        self.drilldown.load_context(ctx)
        self.stack.setCurrentWidget(self.drilldown)

    def on_export_pdf(self):
        default_pdf = str(Path.home() / "Desktop" / "Lecture_Notes.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Export Notes as PDF", default_pdf, "PDF (*.pdf)")
        if path:
            self.gemini.export_pdf(path)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = LectureBuddyApp()
    window.show()
    sys.exit(app.exec())
