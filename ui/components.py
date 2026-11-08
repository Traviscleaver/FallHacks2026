# components.py — Borderless Inner Feed & Standardized Section Rows

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QScrollArea


class CleanSectionRow(QFrame):
    """Standardized flat row inside the Live Sections panel."""
    clicked = Signal(str, str, str)

    def __init__(self, title: str, meta: str, body: str):
        super().__init__()
        self.title_text = title
        self.meta_text = meta
        self.body_text = body

        self.setObjectName("SectionRow")
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        t_lbl = QLabel(title)
        t_lbl.setTextFormat(Qt.PlainText)
        t_lbl.setObjectName("RowTitle")
        top.addWidget(t_lbl)
        top.addStretch()

        m_lbl = QLabel(meta)
        m_lbl.setObjectName("RowMeta")
        top.addWidget(m_lbl)
        layout.addLayout(top)

        b_lbl = QLabel(body)
        b_lbl.setTextFormat(Qt.PlainText)
        b_lbl.setObjectName("RowBody")
        b_lbl.setWordWrap(True)
        layout.addWidget(b_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.title_text, f"TIMESTAMP {self.meta_text}", self.body_text)
        super().mousePressEvent(event)


class SlideScrollFeed(QFrame):
    """Flush inner slide feed — zero outer border so it never creates a double-box."""
    fileDropped = Signal(str)
    viewportClicked = Signal()
    slideClicked = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("InnerCanvas")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self._raw_pixmaps = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setObjectName("InnerCanvas")

        self.container = QWidget()
        self.container.setObjectName("InnerCanvas")
        self.feed_layout = QVBoxLayout(self.container)
        self.feed_layout.setContentsMargins(0, 4, 4, 4)
        self.feed_layout.setSpacing(12)

        self.initial_label = QLabel("Click anywhere or drop a PDF to load lecture slides")
        self.initial_label.setObjectName("MutedText")
        self.initial_label.setAlignment(Qt.AlignCenter)
        self.feed_layout.addStretch()
        self.feed_layout.addWidget(self.initial_label)
        self.feed_layout.addStretch()

        self.scroll_area.setWidget(self.container)
        layout.addWidget(self.scroll_area, stretch=1)

        self.scroll_area.mousePressEvent = self.mousePressEvent

    def set_slides(self, pixmaps: list):
        self._raw_pixmaps = pixmaps
        self._render_scaled_slides()

    def _render_scaled_slides(self):
        if not self._raw_pixmaps:
            while self.feed_layout.count():
                item = self.feed_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            placeholder = QLabel("Click anywhere or drop a PDF to load lecture slides")
            placeholder.setObjectName("MutedText")
            placeholder.setAlignment(Qt.AlignCenter)
            self.feed_layout.addStretch()
            self.feed_layout.addWidget(placeholder)
            self.feed_layout.addStretch()
            return

        while self.feed_layout.count():
            item = self.feed_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        target_width = max(320, self.scroll_area.viewport().width() - 16)

        for idx, pixmap in enumerate(self._raw_pixmaps):
            img_lbl = QLabel()
            img_lbl.setAlignment(Qt.AlignCenter)
            img_lbl.setStyleSheet("background: transparent; border: none;")
            img_lbl.setCursor(Qt.PointingHandCursor)

            scaled = pixmap.scaledToWidth(target_width, Qt.SmoothTransformation)
            img_lbl.setPixmap(scaled)

            img_lbl.mousePressEvent = lambda event, s=idx + 1: self.slideClicked.emit(s)
            self.feed_layout.addWidget(img_lbl)

        self.feed_layout.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._raw_pixmaps:
            self._render_scaled_slides()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(".pdf"):
                self.fileDropped.emit(path)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._raw_pixmaps:
            self.viewportClicked.emit()
        super().mousePressEvent(event)
