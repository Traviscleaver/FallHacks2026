# drilldown_view.py — Standardized Deep-Dive & Chat View with Light/Dark Support

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QTextEdit, QLineEdit
)
from gemini_stub import GeminiServiceStub


def bind_debounced(button: QPushButton, callback):
    def _wrapped():
        if not button.isEnabled():
            return
        button.setEnabled(False)
        QTimer.singleShot(280, lambda: button.setEnabled(True))
        callback()
    button.clicked.connect(_wrapped)


class DrillDownView(QWidget):
    backRequested = Signal()

    def __init__(self, gemini: GeminiServiceStub):
        super().__init__()
        self.gemini = gemini
        self.context_title = "The Perceptron"
        self.current_badge = "SLIDE ANALYSIS"
        self.current_body = "Section detail"
        self.is_light_mode = False
        self.chat_history = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(16)

        # LEFT PANEL: CONCEPT DEEP-DIVE
        explain_card = QFrame()
        explain_card.setObjectName("PanelCard")
        exp_layout = QVBoxLayout(explain_card)
        exp_layout.setContentsMargins(16, 14, 16, 14)
        exp_layout.setSpacing(12)

        top_bar = QWidget()
        top_bar.setFixedHeight(28)
        top_bar.setObjectName("InnerCanvas")
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(0, 0, 0, 0)

        btn_back = QPushButton("← Back")
        btn_back.setObjectName("SubtleActionButton")
        btn_back.setCursor(Qt.PointingHandCursor)
        bind_debounced(btn_back, self.backRequested.emit)
        tb_layout.addWidget(btn_back)

        self.lbl_title = QLabel("Lecture Concept Deep-Dive")
        self.lbl_title.setObjectName("PanelTitle")
        tb_layout.addSpacing(8)
        tb_layout.addWidget(self.lbl_title)
        tb_layout.addStretch()

        self.meta_badge = QLabel("SLIDE ANALYSIS")
        self.meta_badge.setObjectName("WorkspaceBadge")
        tb_layout.addWidget(self.meta_badge)
        exp_layout.addWidget(top_bar)

        div_left = QFrame()
        div_left.setFixedHeight(1)
        div_left.setObjectName("HeaderDivider")
        exp_layout.addWidget(div_left)

        self.content_box = QTextEdit()
        self.content_box.setReadOnly(True)
        self.content_box.setObjectName("FlushTextViewer")
        exp_layout.addWidget(self.content_box, stretch=1)

        # RIGHT PANEL: ASK LECTURE BUDDY CHAT
        chat_card = QFrame()
        chat_card.setObjectName("PanelCard")
        chat_layout = QVBoxLayout(chat_card)
        chat_layout.setContentsMargins(16, 14, 16, 14)
        chat_layout.setSpacing(12)

        chat_header = QWidget()
        chat_header.setFixedHeight(28)
        chat_header.setObjectName("InnerCanvas")
        ch_layout = QHBoxLayout(chat_header)
        ch_layout.setContentsMargins(0, 0, 0, 0)

        lbl_chat = QLabel("Ask Lecture Buddy")
        lbl_chat.setObjectName("PanelTitle")
        ch_layout.addWidget(lbl_chat)
        ch_layout.addStretch()

        lbl_ai_tag = QLabel("CONTEXTUAL Q&A")
        lbl_ai_tag.setObjectName("WorkspaceBadge")
        ch_layout.addWidget(lbl_ai_tag)
        chat_layout.addWidget(chat_header)

        div_right = QFrame()
        div_right.setFixedHeight(1)
        div_right.setObjectName("HeaderDivider")
        chat_layout.addWidget(div_right)

        self.chat_feed = QTextEdit()
        self.chat_feed.setReadOnly(True)
        self.chat_feed.setObjectName("FlushTextViewer")
        chat_layout.addWidget(self.chat_feed, stretch=1)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)

        self.chat_input = QLineEdit()
        self.chat_input.setObjectName("ChatInput")
        self.chat_input.setPlaceholderText("Ask a question...")
        self.chat_input.returnPressed.connect(self.send_message)

        btn_send = QPushButton("Send")
        btn_send.setObjectName("AccentButton")
        btn_send.setCursor(Qt.PointingHandCursor)
        bind_debounced(btn_send, self.send_message)

        input_row.addWidget(self.chat_input, stretch=1)
        input_row.addWidget(btn_send)
        chat_layout.addLayout(input_row)

        layout.addWidget(explain_card, stretch=11)
        layout.addWidget(chat_card, stretch=9)

        self._render_content()
        self._render_chat()

    def debounced_connect(self, button: QPushButton, slot):
        bind_debounced(button, slot)

    def set_theme_mode(self, is_light: bool):
        self.is_light_mode = is_light
        self._render_content()
        self._render_chat()

    def load_item(self, title: str, badge_text: str, body: str):
        self.context_title = title
        self.current_badge = badge_text.upper()
        self.current_body = body
        self.lbl_title.setText(title)
        self.meta_badge.setText(self.current_badge)
        self._render_content()

    def _render_content(self):
        primary = "#09090B" if self.is_light_mode else "#FFFFFF"
        secondary = "#27272A" if self.is_light_mode else "#E4E4E7"
        muted = "#71717A" if self.is_light_mode else "#8E8E93"
        divider = "#E4E4E7" if self.is_light_mode else "#262626"

        self.content_box.setHtml(f"""
            <div style='color:{secondary}; font-size:13px; line-height:1.6;'>
                <p style='color:{muted}; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;'>
                    Lecture Context
                </p>
                <p style='color:{primary}; margin-top:0px; margin-bottom:16px;'>
                    {self.current_body}
                </p>
                <hr style='border:none; border-top:1px solid {divider}; margin:16px 0;'>
                <p style='color:{muted}; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px;'>
                    AI Concept Breakdown
                </p>
                <p style='color:{secondary}; margin-top:0px;'>
                    • <b>Linear Combination:</b> <code style='color:{primary};'>z = W·x + b</code><br>
                    • <b>Activation Function:</b> <code style='color:{primary};'>σ(z) = 1 / (1 + e⁻ᶻ)</code>
                </p>
            </div>
        """)

    def _render_chat(self):
        muted = "#71717A" if self.is_light_mode else "#8E8E93"
        primary = "#09090B" if self.is_light_mode else "#FFFFFF"
        secondary = "#27272A" if self.is_light_mode else "#E4E4E7"

        if not self.chat_history:
            self.chat_feed.setHtml(
                f"<p style='color:{muted}; margin-top:4px;'>Ask any question about this slide or lecture section.</p>"
            )
            return

        blocks = []
        for sender, msg in self.chat_history:
            if sender == "You":
                blocks.append(f"<p style='margin:6px 0;'><b style='color:{primary};'>You:</b> <span style='color:{secondary};'>{msg}</span></p>")
            else:
                blocks.append(f"<p style='margin:6px 0;'><b style='color:{muted};'>Lecture Buddy:</b> <span style='color:{primary};'>{msg}</span></p>")
        self.chat_feed.setHtml("".join(blocks))

    def send_message(self):
        text = self.chat_input.text().strip()
        if not text:
            return
        self.chat_input.clear()
        self.chat_history.append(("You", text))
        reply = self.gemini.ask_chat(text, self.context_title)
        self.chat_history.append(("Buddy", reply))
        self._render_chat()
