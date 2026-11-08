# workspace_view.py — Symmetrical 2-Column Grid with Compact Blue Transcript

from pathlib import Path
from html import escape
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QScrollArea, QFileDialog, QTextEdit
)
from .components import CleanSectionRow, SlideScrollFeed


def bind_debounced(button: QPushButton, callback):
    """Prevents rapid double-clicks from bugging out UI transitions."""
    import time
    previous = [float("-inf")]
    def _wrapped():
        if not button.isEnabled():
            return
        now = time.monotonic()
        if now - previous[0] < 0.28:
            return
        previous[0] = now
        callback()
    button.clicked.connect(_wrapped)


class WorkspaceView(QWidget):
    pdfRequested = Signal(str)
    transcriptRequested = Signal(str)
    resourceSelected = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.pdf_path = None
        self.state = None
        self.total_slides = 0
        self.is_light_mode = False
        self.live_mode = False
        self.live_lines = []
        self.interim_text = ""
        self.recording_message = ""

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

        btn_import = self.btn_import = QPushButton("Import PDF")
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

        # RIGHT COLUMN: LECTURE OUTLINE (TOP) + TRANSCRIPT (BOTTOM)
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(16)

        # 1. LECTURE SECTIONS PANEL
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

        sec_title = QLabel("Lecture Sections")
        sec_title.setObjectName("PanelTitle")
        sec_h_layout.addWidget(sec_title)
        sec_h_layout.addStretch()

        sec_count = self.sec_count = QLabel("0 sections")
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
        sec_list = self.sec_list = QVBoxLayout(sec_container)
        sec_list.setContentsMargins(0, 0, 0, 0)
        sec_list.setSpacing(0)

        sec_list.addWidget(QLabel("Import lecture slides to see their sections."))
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

        trans_title = QLabel("Lecture Transcript")
        trans_title.setObjectName("PanelTitle")
        th_layout.addWidget(trans_title)
        th_layout.addStretch()

        trans_status = self.trans_status = QLabel("IDLE")
        trans_status.setObjectName("WorkspaceBadge")
        th_layout.addWidget(trans_status)
        self.btn_transcript = QPushButton("Import Transcript")
        self.btn_transcript.setEnabled(False)
        self.btn_transcript.clicked.connect(self.browse_transcript)
        th_layout.addWidget(self.btn_transcript)
        trans_layout.addWidget(trans_header)

        div3 = QFrame()
        div3.setFixedHeight(1)
        div3.setObjectName("HeaderDivider")
        trans_layout.addWidget(div3)

        self.transcript_box = QTextEdit()
        self.transcript_box.setReadOnly(True)
        self.transcript_box.setObjectName("TranscriptViewer")
        self.transcript_lines = []
        self.render_transcript()
        trans_layout.addWidget(self.transcript_box, stretch=1)

        right_col.addWidget(sections_card, stretch=6)
        right_col.addWidget(trans_card, stretch=4)

        layout.addWidget(slide_card, stretch=11)
        layout.addLayout(right_col, stretch=9)

    def render_transcript(self):
        color = "#2563EB" if self.is_light_mode else "#60A5FA"
        html_blocks = [
            f"<div style='color:{color}; margin-bottom:5px; line-height:1.25;'>{escape(ts)}: {escape(txt)}</div>"
            for ts, txt in self.transcript_lines
        ]
        if self.interim_text:
            html_blocks.append(f"<p style='color:#8E8E93'><i>{escape(self.interim_text)}</i></p>")
        if self.recording_message:
            html_blocks.append(f"<p style='color:#F87171'>{escape(self.recording_message)}</p>")
        self.transcript_box.setHtml("".join(html_blocks) or "Start Recording for a live transcript, or import a transcript text file.")
        bar = self.transcript_box.verticalScrollBar()
        bar.setValue(bar.maximum())

    def begin_live_transcript(self):
        if not self.live_mode:
            self.live_lines = list(self.transcript_lines)
        self.live_mode = True
        self.interim_text = self.recording_message = ""
        self.transcript_lines = list(self.live_lines)
        self.render_transcript()

    def on_transcript_interim(self, text):
        self.interim_text = " ".join(text.split())
        self.render_transcript()

    def on_transcript_final(self, text, start_seconds):
        minutes, seconds = divmod(int(start_seconds), 60)
        self.live_lines.append((f"[{minutes:02d}:{seconds:02d}]", text))
        self.transcript_lines = list(self.live_lines)
        self.interim_text = ""
        self.render_transcript()

    def on_recording_status(self, status):
        labels = {"connecting": "CONNECTING", "recording": "LIVE STREAM", "reconnecting": "RECONNECTING",
                  "stopping": "FINISHING", "stopped": "TRANSCRIPT READY", "error": "ERROR"}
        self.trans_status.setText(labels.get(status, status.upper()))
        if status in ("stopped", "error"):
            self.interim_text = ""
        if status == "recording":
            self.recording_message = ""
        self.render_transcript()

    def on_recording_error(self, message):
        self.recording_message = message
        self.trans_status.setToolTip(message)
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
        if self.state and self.state.index and 0 < slide_num <= len(self.state.index.slides):
            self.resourceSelected.emit("slide", self.state.index.slides[slide_num - 1].id)

    def load_pdf(self, path: str):
        if self.btn_import.isEnabled():
            self.pdf_path = path
            self.pdfRequested.emit(path)

    def show_pdf(self, source):
        self.total_slides = len(source.pages)
        self.viewport.set_slides([QPixmap.fromImage(QImage.fromData(p.image_png)) for p in source.pages])
        name = Path(self.pdf_path).name if self.pdf_path else "Lecture PDF"
        self.lbl_details.setText(f"Loaded: {name} • {self.total_slides} slides")

    def browse_transcript(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import lecture transcript", "", "Text files (*.txt);;All files (*)")
        if path:
            self.transcriptRequested.emit(path)

    def show_state(self, state):
        if self.state is None or self.state.lecture_id != state.lecture_id:
            self.viewport.set_slides([])
            self.lbl_details.setText("Opening PDF…" if self.pdf_path else "No PDF loaded")
            self.live_mode = False
            self.live_lines = []
            self.interim_text = self.recording_message = ""
            self.trans_status.setText("IDLE")
        self.state = state
        self.transcript_lines = (list(self.live_lines) if self.live_mode else
            [(f"Paragraph {s.sequence + 1}", s.text) for s in state.segments])
        self.render_transcript()
        while self.sec_list.count():
            item = self.sec_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = []
        # This panel is the prepared lecture outline, independent of transcript
        # discussion occurrences and their open/closed/matched state.
        if state.index:
            page_numbers = {slide.id: slide.page_number for slide in state.index.slides}
            for group in state.index.subsections:
                pages = sorted({page_numbers[sid] for sid in group.slide_ids})
                meta = ("Page " if len(pages) == 1 else "Pages ") + ", ".join(map(str, pages))
                rows.append(("subsection", group.id, group.title, meta, group.summary[:250]))
        for kind, ident, title, meta, body in rows:
            row = CleanSectionRow(title, meta, body)
            row.clicked.connect(lambda *args, k=kind, key=ident: self.resourceSelected.emit(k, key))
            self.sec_list.addWidget(row)
        if not rows:
            self.sec_list.addWidget(QLabel("Lecture sections will appear here after slide preparation."))
        self.sec_list.addStretch()
        self.sec_count.setText(f"{len(rows)} section" + ("" if len(rows) == 1 else "s"))
