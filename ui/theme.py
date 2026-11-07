# theme.py — Standardized Dark Grey & Inverted Light Mode Palettes

MODERN_DARK_GREY_QSS = """
QMainWindow, QWidget {
    background-color: #121212;
    color: #EDEDED;
    font-family: '-apple-system', 'SF Pro Display', 'Inter', 'Segoe UI', sans-serif;
    font-size: 13px;
}

#TopHeader {
    background-color: #121212;
    border-bottom: 1px solid #262626;
}
#AppTitle {
    font-size: 16px;
    font-weight: 600;
    color: #FFFFFF;
    background: transparent;
}
#WorkspaceBadge {
    font-size: 11px;
    font-weight: 600;
    color: #8E8E93;
    background: transparent;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}

#PanelCard {
    background-color: #1A1A1A;
    border: 1px solid #2A2A2A;
    border-radius: 10px;
}

#InnerCanvas {
    background-color: #1A1A1A;
    border: none;
    border-radius: 0px;
}

#HeaderDivider {
    background-color: #262626;
    border: none;
}

#PanelTitle {
    font-size: 13px;
    font-weight: 600;
    color: #F5F5F7;
    background: transparent;
    border: none;
}
#MutedText {
    font-size: 12px;
    color: #8E8E93;
    background: transparent;
    border: none;
}

#SectionRow {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #262626;
    border-radius: 0px;
}
#SectionRow:hover {
    background-color: #222222;
}
#RowTitle {
    font-size: 13px;
    font-weight: 600;
    color: #FFFFFF;
    background: transparent;
}
#RowMeta {
    font-family: 'Menlo', 'Monaco', monospace;
    font-size: 11px;
    color: #8E8E93;
    background: transparent;
}
#RowBody {
    font-size: 12px;
    color: #A1A1AA;
    background: transparent;
}

#FlushTextViewer {
    background-color: transparent;
    border: none;
    padding: 0px;
    color: #E4E4E7;
    font-size: 13px;
}
#TranscriptViewer {
    font-family: 'Menlo', 'Monaco', monospace;
    font-size: 12px;
    color: #60A5FA;
    background-color: transparent;
    border: none;
    padding: 0px;
}

QPushButton {
    background-color: #262626;
    color: #EDEDED;
    border: 1px solid #363636;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #303030;
    border-color: #4A4A4A;
}

#AccentButton {
    background-color: #EDEDED;
    color: #121212;
    border: 1px solid #FFFFFF;
    font-weight: 600;
}
#AccentButton:hover {
    background-color: #FFFFFF;
}
#SubtleActionButton {
    background-color: #242424;
    color: #E4E4E7;
    border: 1px solid #333333;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
}
#SubtleActionButton:hover {
    background-color: #2E2E2E;
    border-color: #454545;
}

/* Start Recording Button — Locked across both Dark & Light modes */
#RecordButton {
    background-color: #2A1617;
    color: #F87171;
    border: 1px solid #5C2324;
    font-weight: 600;
}
#RecordButton:hover {
    background-color: #3B1B1D;
}

#ChatInput {
    background-color: #121212;
    border: 1px solid #2E2E2E;
    border-radius: 6px;
    padding: 8px 12px;
    color: #FFFFFF;
    font-size: 13px;
}
#ChatInput:focus {
    border: 1px solid #52525B;
}

QScrollArea {
    background: transparent;
    border: none;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent;
    width: 6px;
    height: 6px;
    margin: 0px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #333333;
    border-radius: 3px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #4A4A4A;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0px;
    width: 0px;
}
"""


MODERN_LIGHT_QSS = """
QMainWindow, QWidget {
    background-color: #F4F4F5;
    color: #18181B;
    font-family: '-apple-system', 'SF Pro Display', 'Inter', 'Segoe UI', sans-serif;
    font-size: 13px;
}

#TopHeader {
    background-color: #F4F4F5;
    border-bottom: 1px solid #E4E4E7;
}
#AppTitle {
    font-size: 16px;
    font-weight: 600;
    color: #09090B;
    background: transparent;
}
#WorkspaceBadge {
    font-size: 11px;
    font-weight: 600;
    color: #71717A;
    background: transparent;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}

#PanelCard {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 10px;
}

#InnerCanvas {
    background-color: #FFFFFF;
    border: none;
    border-radius: 0px;
}

#HeaderDivider {
    background-color: #E4E4E7;
    border: none;
}

#PanelTitle {
    font-size: 13px;
    font-weight: 600;
    color: #09090B;
    background: transparent;
    border: none;
}
#MutedText {
    font-size: 12px;
    color: #71717A;
    background: transparent;
    border: none;
}

#SectionRow {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #E4E4E7;
    border-radius: 0px;
}
#SectionRow:hover {
    background-color: #F8FAFC;
}
#RowTitle {
    font-size: 13px;
    font-weight: 600;
    color: #09090B;
    background: transparent;
}
#RowMeta {
    font-family: 'Menlo', 'Monaco', monospace;
    font-size: 11px;
    color: #71717A;
    background: transparent;
}
#RowBody {
    font-size: 12px;
    color: #52525B;
    background: transparent;
}

#FlushTextViewer {
    background-color: transparent;
    border: none;
    padding: 0px;
    color: #27272A;
    font-size: 13px;
}
#TranscriptViewer {
    font-family: 'Menlo', 'Monaco', monospace;
    font-size: 12px;
    color: #2563EB;
    background-color: transparent;
    border: none;
    padding: 0px;
}

QPushButton {
    background-color: #FFFFFF;
    color: #18181B;
    border: 1px solid #D4D4D8;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #F4F4F5;
    border-color: #A1A1AA;
}

#AccentButton {
    background-color: #18181B;
    color: #FFFFFF;
    border: 1px solid #09090B;
    font-weight: 600;
}
#AccentButton:hover {
    background-color: #27272A;
}
#SubtleActionButton {
    background-color: #F4F4F5;
    color: #18181B;
    border: 1px solid #E4E4E7;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
}
#SubtleActionButton:hover {
    background-color: #E4E4E7;
    border-color: #D4D4D8;
}

/* Start Recording Button — Unchanged from Dark Mode */
#RecordButton {
    background-color: #2A1617;
    color: #F87171;
    border: 1px solid #5C2324;
    font-weight: 600;
}
#RecordButton:hover {
    background-color: #3B1B1D;
}

#ChatInput {
    background-color: #F4F4F5;
    border: 1px solid #D4D4D8;
    border-radius: 6px;
    padding: 8px 12px;
    color: #09090B;
    font-size: 13px;
}
#ChatInput:focus {
    border: 1px solid #71717A;
}

QScrollArea {
    background: transparent;
    border: none;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent;
    width: 6px;
    height: 6px;
    margin: 0px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #D4D4D8;
    border-radius: 3px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #A1A1AA;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0px;
    width: 0px;
}
"""
