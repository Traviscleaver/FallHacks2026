# drilldown_view.py — Standardized Deep-Dive & Chat View with Light/Dark Support

import html

from PySide6.QtCore import Qt, Signal, QTimer, Slot
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QTextEdit, QLineEdit
)
from slide_deep_dive import DeepDiveContext, DeepDiveService
from slide_deep_dive.render import breakdown_to_html, markdown_lite_to_html


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

    def __init__(self, deep_dive: DeepDiveService):
        super().__init__()
        self.deep_dive = deep_dive
        self.context = DeepDiveContext(title="", badge="", description="")
        self.is_light_mode = False

        # Breakdown state: "idle" | "loading" | "ready" | "error"
        self.breakdown_state = "idle"
        self.breakdown = None
        self.breakdown_error = ""
        self.breakdown_request = 0

        # Chat: list of (role, text, status) with role "user"/"model" and
        # status "ok" | "pending" | "error".
        self.chat_history = []
        self.chat_request = 0

        self.deep_dive.breakdown_ready.connect(self.on_breakdown_ready)
        self.deep_dive.breakdown_failed.connect(self.on_breakdown_failed)
        self.deep_dive.chat_ready.connect(self.on_chat_ready)
        self.deep_dive.chat_failed.connect(self.on_chat_failed)

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
        tb_layout.addWidget(self.lbl_title, stretch=1)

        self.btn_refresh = QPushButton("↻ Refresh")
        self.btn_refresh.setObjectName("SubtleActionButton")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.setToolTip("Regenerate using the transcript recorded so far")
        bind_debounced(self.btn_refresh, self.request_breakdown)
        tb_layout.addWidget(self.btn_refresh)

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

        lbl_chat = QLabel("Ask Borp")
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

        self.btn_send = QPushButton("Send")
        self.btn_send.setObjectName("AccentButton")
        self.btn_send.setCursor(Qt.PointingHandCursor)
        bind_debounced(self.btn_send, self.send_message)

        input_row.addWidget(self.chat_input, stretch=1)
        input_row.addWidget(self.btn_send)
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

    # ------------------------------------------------------------ loading

    def load_context(self, ctx: DeepDiveContext):
        """Show a new slide/section. Any in-flight answers for the old one are ignored."""
        self.context = ctx
        self.lbl_title.setText(ctx.title)
        self.meta_badge.setText(ctx.badge.upper())
        self.chat_history = []
        self.chat_request = 0
        self.chat_input.clear()
        self._render_chat()
        self.request_breakdown()

    def load_item(self, title: str, badge_text: str, body: str, transcript_provider=None):
        """Text-only context (e.g. a live section row)."""
        ctx = DeepDiveContext(title=title, badge=badge_text, description=body)
        if transcript_provider is not None:
            ctx.transcript_provider = transcript_provider
        self.load_context(ctx)

    def update_context(self, ctx: DeepDiveContext):
        """Refresh source material without restarting the conversation or making API calls."""
        self.context = ctx
        self.lbl_title.setText(ctx.title)
        self.meta_badge.setText(ctx.badge.upper())
        self._render_content()

    def request_breakdown(self):
        if not self.context.title:
            return
        self.breakdown_state = "loading"
        self.breakdown = None
        self.breakdown_request = self.deep_dive.request_breakdown(self.context)
        self._render_content()

    @Slot(int, object)
    def on_breakdown_ready(self, rid: int, breakdown):
        if rid != self.breakdown_request:
            return
        self.breakdown_state = "ready"
        self.breakdown = breakdown
        self._render_content()

    @Slot(int, str)
    def on_breakdown_failed(self, rid: int, message: str):
        if rid != self.breakdown_request:
            return
        self.breakdown_state = "error"
        self.breakdown_error = message
        self._render_content()

    # ---------------------------------------------------------- rendering

    def _colors(self):
        light = self.is_light_mode
        return {
            "primary": "#09090B" if light else "#FFFFFF",
            "secondary": "#27272A" if light else "#E4E4E7",
            "muted": "#71717A" if light else "#8E8E93",
            "divider": "#E4E4E7" if light else "#262626",
            "error": "#DC2626" if light else "#F87171",
        }

    def _render_content(self):
        c = self._colors()
        if self.breakdown_state == "ready" and self.breakdown is not None:
            body = breakdown_to_html(self.breakdown, c["primary"], c["secondary"], c["muted"])
        elif self.breakdown_state == "loading":
            body = f"<p style='color:{c['muted']};'><i>Analyzing this slide with Gemini…</i></p>"
        elif self.breakdown_state == "error":
            body = (
                f"<p style='color:{c['error']};'>Could not generate the breakdown: "
                f"{html.escape(self.breakdown_error)}</p>"
                f"<p style='color:{c['muted']};'>Press ↻ Refresh to try again.</p>"
            )
        else:
            body = f"<p style='color:{c['muted']};'>Select a slide to analyze.</p>"

        self.content_box.setHtml(f"""
            <div style='color:{c['secondary']}; font-size:13px; line-height:1.6;'>
                <p style='color:{c['muted']}; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;'>
                    Lecture Context
                </p>
                <p style='color:{c['primary']}; margin-top:0px; margin-bottom:16px;'>
                    {markdown_lite_to_html(self.context.description)}
                </p>
                <hr style='border:none; border-top:1px solid {c['divider']}; margin:16px 0;'>
                <p style='color:{c['muted']}; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px;'>
                    AI Concept Breakdown
                </p>
                {body}
            </div>
        """)

    def _render_chat(self):
        c = self._colors()
        if not self.chat_history:
            self.chat_feed.setHtml(
                f"<p style='color:{c['muted']}; margin-top:4px;'>Ask any question about this slide or lecture section.</p>"
            )
            return

        blocks = []
        for role, msg, status in self.chat_history:
            if role == "user":
                blocks.append(
                    f"<p style='margin:6px 0;'><b style='color:{c['primary']};'>You:</b> "
                    f"<span style='color:{c['secondary']};'>{html.escape(msg)}</span></p>"
                )
            elif status == "pending":
                blocks.append(
                    f"<p style='margin:6px 0;'><b style='color:{c['muted']};'>Borp:</b> "
                    f"<i style='color:{c['muted']};'>Thinking…</i></p>"
                )
            elif status == "error":
                blocks.append(
                    f"<p style='margin:6px 0;'><b style='color:{c['muted']};'>Borp:</b> "
                    f"<span style='color:{c['error']};'>Couldn't get an answer: {html.escape(msg)}</span></p>"
                )
            else:
                blocks.append(
                    f"<div style='margin:6px 0;'><b style='color:{c['muted']};'>Borp:</b> "
                    f"<span style='color:{c['primary']};'>{markdown_lite_to_html(msg)}</span></div>"
                )
        self.chat_feed.setHtml("".join(blocks))
        bar = self.chat_feed.verticalScrollBar()
        bar.setValue(bar.maximum())

    # --------------------------------------------------------------- chat

    def _chat_pending(self) -> bool:
        return bool(self.chat_history) and self.chat_history[-1][2] == "pending"

    def send_message(self):
        text = self.chat_input.text().strip()
        if not text or not self.context.title or self._chat_pending():
            return
        self.chat_input.clear()
        # Only completed question/answer pairs are sent as history.
        history = []
        for i in range(0, len(self.chat_history) - 1):
            (r1, q, _), (r2, a, s2) = self.chat_history[i], self.chat_history[i + 1]
            if r1 == "user" and r2 == "model" and s2 == "ok":
                history += [("user", q), ("model", a)]
        self.chat_history.append(("user", text, "ok"))
        self.chat_history.append(("model", "", "pending"))
        self.chat_request = self.deep_dive.ask(self.context, text, history)
        self._render_chat()

    def _finish_chat(self, rid: int, text: str, status: str):
        if rid != self.chat_request or not self._chat_pending():
            return
        self.chat_history[-1] = ("model", text, status)
        self._render_chat()

    @Slot(int, str)
    def on_chat_ready(self, rid: int, text: str):
        self._finish_chat(rid, text, "ok")

    @Slot(int, str)
    def on_chat_failed(self, rid: int, message: str):
        self._finish_chat(rid, message, "error")
