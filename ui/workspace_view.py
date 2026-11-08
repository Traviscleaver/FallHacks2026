# workspace_view.py — Symmetrical 2-Column Grid with Compact Blue Transcript

from pathlib import Path
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QScrollArea, QFileDialog, QTextEdit
)
from components import CleanSectionRow, SlideScrollFeed
from gemini_stub import GeminiServiceStub
import fitz
from PIL import Image
import html
import io


def bind_debounced(button: QPushButton, callback):
    """Prevents rapid double-clicks from bugging out UI transitions."""
    def _wrapped():
        if not button.isEnabled():
            return
        button.setEnabled(False)
        QTimer.singleShot(280, lambda: button.setEnabled(True))
        callback()
    button.clicked.connect(_wrapped)


class WorkspaceView(QWidget):
    openDrillDown = Signal(str, str, str)

    def __init__(self, gemini: GeminiServiceStub):
        super().__init__()
        self.gemini = gemini
        self.pdf_path = None
        self.doc = None
        self.total_slides = 0
        self.is_light_mode = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(16)

        # LEFT COLUMN: LECTURE SLIDES PANEL
        slide_card = QFrame()
        slide_card.setObjectName("PanelCard")
        slide_layout = QVBoxLayout(slide_card)
        slide_layout.setContentsMargins(16, 14, 16, 14)
        slide_layout.setSpacing(12)

        slide_header = QWidget()
        slide_header.setFixedHeight(28)
        slide_header.setObjectName("InnerCanvas")
        sh_layout = QHBoxLayout(slide_header)
        sh_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Lecture Slides")
        title.setObjectName("PanelTitle")
        sh_layout.addWidget(title)
        sh_layout.addStretch()

        btn_import = QPushButton("Import PDF")
        btn_import.setObjectName("SubtleActionButton")
        btn_import.setCursor(Qt.PointingHandCursor)
        bind_debounced(btn_import, self.browse_pdf)
        sh_layout.addWidget(btn_import)
        slide_layout.addWidget(slide_header)

        div1 = QFrame()
        div1.setFixedHeight(1)
        div1.setObjectName("HeaderDivider")
        slide_layout.addWidget(div1)

        self.viewport = SlideScrollFeed()
        self.viewport.fileDropped.connect(self.load_pdf)
        self.viewport.viewportClicked.connect(self.browse_pdf)
        self.viewport.slideClicked.connect(self.on_slide_clicked)
        slide_layout.addWidget(self.viewport, stretch=1)

        self.lbl_details = QLabel("No PDF loaded — Drop file or click to import")
        self.lbl_details.setObjectName("MutedText")
        slide_layout.addWidget(self.lbl_details)

        # RIGHT COLUMN: LIVE SECTIONS (TOP) + TRANSCRIPT (BOTTOM)
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(16)

        # 1. LIVE SECTIONS PANEL
        sections_card = QFrame()
        sections_card.setObjectName("PanelCard")
        sec_layout = QVBoxLayout(sections_card)
        sec_layout.setContentsMargins(16, 14, 16, 14)
        sec_layout.setSpacing(10)

        sec_header = QWidget()
        sec_header.setFixedHeight(28)
        sec_header.setObjectName("InnerCanvas")
        sec_h_layout = QHBoxLayout(sec_header)
        sec_h_layout.setContentsMargins(0, 0, 0, 0)

        sec_title = QLabel("Live Sections")
        sec_title.setObjectName("PanelTitle")
        sec_h_layout.addWidget(sec_title)
        sec_h_layout.addStretch()

        sec_count = QLabel("3 topics")
        sec_count.setObjectName("MutedText")
        sec_h_layout.addWidget(sec_count)
        sec_layout.addWidget(sec_header)

        div2 = QFrame()
        div2.setFixedHeight(1)
        div2.setObjectName("HeaderDivider")
        sec_layout.addWidget(div2)

        sec_scroll = QScrollArea()
        sec_scroll.setWidgetResizable(True)
        sec_container = QWidget()
        sec_container.setObjectName("InnerCanvas")
        sec_list = QVBoxLayout(sec_container)
        sec_list.setContentsMargins(0, 0, 0, 0)
        sec_list.setSpacing(0)

        sections_data = [
            ("Course Logistics & Rubric", "00:00", "Grading breakdown and midterm schedule."),
            ("Introduction to Material", "03:16", "Core definitions and foundational concepts."),
            ("The Perceptron Model", "08:12", "Dot product of weights and inputs plus bias."),
        ]
        for t, meta, body in sections_data:
            row = CleanSectionRow(t, meta, body)
            row.clicked.connect(self.openDrillDown.emit)
            sec_list.addWidget(row)

        sec_list.addStretch()
        sec_scroll.setWidget(sec_container)
        sec_layout.addWidget(sec_scroll, stretch=1)

        # 2. LIVE AUDIO TRANSCRIPT PANEL
        trans_card = QFrame()
        trans_card.setObjectName("PanelCard")
        trans_layout = QVBoxLayout(trans_card)
        trans_layout.setContentsMargins(16, 14, 16, 14)
        trans_layout.setSpacing(8)

        trans_header = QWidget()
        trans_header.setFixedHeight(28)
        trans_header.setObjectName("InnerCanvas")
        th_layout = QHBoxLayout(trans_header)
        th_layout.setContentsMargins(0, 0, 0, 0)

        trans_title = QLabel("Live Audio Transcript")
        trans_title.setObjectName("PanelTitle")
        th_layout.addWidget(trans_title)
        th_layout.addStretch()

        self.trans_status = QLabel("IDLE")
        self.trans_status.setObjectName("WorkspaceBadge")
        th_layout.addWidget(self.trans_status)
        trans_layout.addWidget(trans_header)

        div3 = QFrame()
        div3.setFixedHeight(1)
        div3.setObjectName("HeaderDivider")
        trans_layout.addWidget(div3)

        self.transcript_box = QTextEdit()
        self.transcript_box.setReadOnly(True)
        self.transcript_box.setObjectName("TranscriptViewer")
        self.transcript_lines: list[tuple[str, str]] = []
        self.interim_text = ""
        self.render_transcript()
        trans_layout.addWidget(self.transcript_box, stretch=1)

        self.gemini.transcript_interim.connect(self.on_transcript_interim)
        self.gemini.transcript_final.connect(self.on_transcript_final)
        self.gemini.recording_status.connect(self.on_recording_status)
        self.gemini.recording_error.connect(self.on_recording_error)

        right_col.addWidget(sections_card, stretch=6)
        right_col.addWidget(trans_card, stretch=4)

        layout.addWidget(slide_card, stretch=11)
        layout.addLayout(right_col, stretch=9)

    def render_transcript(self):
        color = "#2563EB" if self.is_light_mode else "#60A5FA"
        muted = "#71717A" if self.is_light_mode else "#A1A1AA"
        html_blocks = [
            f"<div style='color:{color}; margin-bottom:5px; line-height:1.25;'>"
            f"{ts}: {html.escape(txt)}</div>"
            for ts, txt in self.transcript_lines
        ]
        if self.interim_text:
            html_blocks.append(
                f"<div style='color:{muted}; font-style:italic; line-height:1.25;'>"
                f"{html.escape(self.interim_text)}</div>"
            )
        if not html_blocks:
            html_blocks.append(
                f"<div style='color:{muted};'>Press Start Recording to begin the live transcript.</div>"
            )
        self.transcript_box.setHtml("".join(html_blocks))
        bar = self.transcript_box.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ----------------------------------------------------- live transcript

    def on_transcript_interim(self, text: str):
        self.interim_text = " ".join(text.split())
        self.render_transcript()

    def on_transcript_final(self, text: str, start_seconds: float):
        m, s = divmod(int(start_seconds), 60)
        self.transcript_lines.append((f"[{m:02d}:{s:02d}]", text))
        self.interim_text = ""
        self.render_transcript()

    def on_recording_status(self, status: str):
        labels = {
            "connecting": "CONNECTING",
            "recording": "LIVE STREAM",
            "reconnecting": "RECONNECTING",
            "stopping": "FINISHING",
            "stopped": "IDLE",
            "error": "ERROR",
        }
        self.trans_status.setText(labels.get(status, status.upper()))
        if status == "stopped" and self.interim_text:
            # Nothing more will finalize it, so drop the stale interim line.
            self.interim_text = ""
            self.render_transcript()

    def on_recording_error(self, message: str):
        self.trans_status.setToolTip(message)
        self.transcript_lines.append(("[!]", message))
        self.render_transcript()

    def set_theme_mode(self, is_light: bool):
        self.is_light_mode = is_light
        self.render_transcript()

    def debounced_connect(self, button: QPushButton, slot):
        bind_debounced(button, slot)

    def browse_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Lecture Slides PDF", str(Path.home()), "PDF Files (*.pdf)"
        )
        if path:
            self.load_pdf(path)

    def on_slide_clicked(self, slide_num: int):
        self.openDrillDown.emit(
            f"Slide {slide_num} Deep-Dive",
            f"SLIDE {slide_num} OF {self.total_slides}",
            f"Selected slide {slide_num} from the vertical lecture feed."
        )

    def load_pdf(self, path: str):
        try:
            self.pdf_path = path
            self.doc = fitz.open(path)
            self.total_slides = len(self.doc)
            pixmaps = []
            for page_num in range(self.total_slides):
                page = self.doc.load_page(page_num)
                pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                buffer = io.BytesIO()
                img.save(buffer, format="PNG")
                qimg = QImage.fromData(buffer.getvalue())
                pixmaps.append(QPixmap.fromImage(qimg))

            self.viewport.set_slides(pixmaps)
            self.lbl_details.setText(f"Loaded: {Path(path).name}  •  {self.total_slides} Slides")
            self.gemini.import_slides(path)
        except Exception as e:
            self.lbl_details.setText("Error loading PDF")
            print(f"PDF Load Error: {e}")
