# main.py — Standardized Shell with Smooth Color-Crossfade Theme Animation

import sys
import os
from pathlib import Path
# Keep the original script entry point working alongside python -m ui.main.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "ui"
from PySide6.QtCore import Qt, QTimer, Slot, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFileDialog, QMessageBox
)
from .theme import MODERN_DARK_GREY_QSS, MODERN_LIGHT_QSS
from .backend import PartCBackend
from .workspace_view import WorkspaceView, bind_debounced
from .drilldown_view import DrillDownView
from .gemini_stub import GeminiServiceStub
from slide_deep_dive import DeepDiveService


class LectureBuddyApp(QMainWindow):
    def __init__(self, backend=None, deep_dive=None, recorder=None):
        super().__init__()
        self.setWindowTitle("Lecture Buddy")
        self.resize(1280, 800)

        self.backend = backend or PartCBackend(parent=self)
        self.recorder = recorder or GeminiServiceStub()
        self._selected_resource = None
        self._last_lecture = None
        self._backend_closed = False
        self.is_recording = False
        self.is_finishing = False
        self._close_requested = False
        self._shutdown_started = False
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

        self.btn_record = QPushButton("● Start Recording (00:00)")
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
        self.workspace = WorkspaceView()
        self.deep_dive = deep_dive or DeepDiveService(
            model=os.getenv("GEMINI_DEEP_DIVE_MODEL") or self.backend.settings.model, parent=self)
        self.drilldown = DrillDownView(self.deep_dive)

        self.stack.addWidget(self.workspace)
        self.stack.addWidget(self.drilldown)
        root_layout.addWidget(self.stack, stretch=1)

        self.workspace.pdfRequested.connect(self.backend.import_pdf)
        self.workspace.transcriptRequested.connect(self.backend.import_transcript)
        self.workspace.resourceSelected.connect(self.open_resource)
        self.backend.pdf_ready.connect(self.workspace.show_pdf)
        self.backend.state_ready.connect(self.show_state)
        self.backend.status_changed.connect(self.statusBar().showMessage)
        self.backend.capabilities_changed.connect(self.update_controls)
        self.backend.closed.connect(self._on_backend_closed)
        self.recorder.transcript_interim.connect(self.workspace.on_transcript_interim)
        self.recorder.transcript_final.connect(self.workspace.on_transcript_final)
        self.recorder.transcript_segment.connect(self.backend.accept_live_segment)
        self.recorder.gap_recorded.connect(self.backend.accept_live_gap)
        self.recorder.recording_status.connect(self.on_recording_status)
        self.recorder.recording_error.connect(self.on_recording_error)
        self.recorder.recording_finished.connect(self.on_recording_finished)
        self.drilldown.backRequested.connect(lambda: self.stack.setCurrentWidget(self.workspace))

        # Initial stylesheet setup
        self.setStyleSheet(MODERN_DARK_GREY_QSS)
        self.statusBar().showMessage("Import a PDF to prepare its slide topics.")
        self.update_controls()

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
            return
        if self.is_recording:
            self.is_recording = False
            self.is_finishing = True
            self.timer.stop()
            self.btn_record.setText(f"… Finishing ({self.fmt(self.seconds)})")
            self.update_controls()
            self.recorder.stop_recording()
        elif self.backend.begin_live_recording():
            self.workspace.begin_live_transcript()
            self.is_recording = True
            if self.recorder.start_recording():
                self.timer.start(1000)
                self.btn_record.setText(f"■ Stop Recording ({self.fmt(self.seconds)})")
            else:
                self.is_recording = False
                self.backend.cancel_live_start()
                self.btn_record.setText(f"● Start Recording ({self.fmt(self.seconds)})")
            self.update_controls()

    @Slot(str)
    def on_recording_status(self, status):
        self.workspace.on_recording_status(status)
        if status == "stopping":
            self.is_recording, self.is_finishing = False, True
            self.timer.stop()
        elif status in ("stopped", "error"):
            self.is_recording = self.is_finishing = False
            self.timer.stop()
            if status == "error":
                self.backend.cancel_live_start()
        self.btn_record.setText(("… Finishing" if self.is_finishing else
            "■ Stop Recording" if self.is_recording else "● Start Recording") + f" ({self.fmt(self.seconds)})")
        self.update_controls()

    @Slot(str)
    def on_recording_error(self, message):
        self.workspace.on_recording_error(message)
        self.statusBar().showMessage("Recording: " + message)

    @Slot(bool)
    def on_recording_finished(self, complete):
        self.backend.finish_live_recording(complete)

    def tick(self):
        self.seconds += 1
        self.btn_record.setText(f"■ Stop Recording ({self.fmt(self.seconds)})")

    @staticmethod
    def fmt(sec: int) -> str:
        m, s = divmod(sec, 60)
        return f"{m:02d}:{s:02d}"

    def open_detail(self, title: str, badge: str, body: str):
        self.drilldown.load_item(title, badge, body)
        self.stack.setCurrentWidget(self.drilldown)

    def open_resource(self, kind, entity_id):
        self._selected_resource = (kind, entity_id)
        self.drilldown.load_context(self.backend.deep_dive_context(kind, entity_id))
        self.stack.setCurrentWidget(self.drilldown)

    def show_state(self, state):
        if state.lecture_id != self._last_lecture:
            self._last_lecture = state.lecture_id
            self._selected_resource = None
            self.stack.setCurrentWidget(self.workspace)
        self.workspace.show_state(state)
        if self._selected_resource:
            self.drilldown.update_context(self.backend.deep_dive_context(*self._selected_resource))

    def update_controls(self):
        active = self.is_recording or self.is_finishing or self.backend.recording_active
        busy = self.backend.preparing or self.backend.analyzing or self.backend.closing or active
        self.workspace.btn_import.setEnabled(not busy)
        state = self.backend.state
        can_import = state is not None and state.index is not None and state.index.status in ("ready", "partial")
        self.workspace.btn_transcript.setEnabled(bool(can_import and not busy and not state.finalized and not self.backend.live_segments))
        self.btn_export.setEnabled(bool(self.backend.current_notes()) and not busy)
        self.btn_record.setEnabled(not self.is_finishing and not self.backend.analyzing
            and not self.backend.closing and not self._close_requested)
        if (self._close_requested and not self._shutdown_started and not active
                and not self.backend.analyzing):
            QTimer.singleShot(0, self.close)

    def on_export_pdf(self):
        default_pdf = str(Path.cwd() / "Lecture_Notes.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Export Notes as PDF", default_pdf, "PDF (*.pdf)")
        if path:
            try:
                self.backend.export_pdf(path)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def closeEvent(self, event):
        if self._backend_closed:
            event.accept()
        else:
            event.ignore()
            self._close_requested = True
            if self.is_recording:
                self.toggle_recording()
                return
            if self.is_finishing or self.backend.analyzing or self._shutdown_started:
                return
            self._shutdown_started = True
            self.statusBar().showMessage("Finishing the current request before closing…")
            self.recorder.shutdown()
            self.deep_dive.shutdown()
            self.backend.shutdown()

    def _on_backend_closed(self):
        self._backend_closed = True
        self.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lecture Buddy: slides, live recording, notes and chat")
    parser.add_argument("--pdf", help="Open a lecture PDF on startup")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    try:
        window = LectureBuddyApp()
    except (ValueError, ImportError) as exc:
        QMessageBox.critical(None, "Setup required", f"Check .env and the project dependencies.\n{type(exc).__name__}")
        raise SystemExit(1)
    window.show()
    if args.pdf:
        QTimer.singleShot(0, lambda: window.workspace.load_pdf(args.pdf))
    sys.exit(app.exec())
