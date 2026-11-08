"""Optional A-side extraction helper. Creates/uses every Qt PDF object in this thread.

Call in a finite worker with an existing QGuiApplication. Pass the returned plain
data to C; never pass a viewer-owned QPdfDocument across threads. The caller
supplies an approved PDF path and persists rendered assets through B if desired.
"""
from hashlib import sha256
from pathlib import Path
from .slide_preparation import PDFInput, PageInput


def extract_pdf(path, save_image=None, max_dimension=1400):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize
    from PySide6.QtPdf import QPdfDocument
    raw = Path(path).read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise ValueError("Not a PDF")
    # Load the exact bytes hashed, avoiding a path replacement between hash and extraction.
    source = QBuffer()
    source.setData(QByteArray(raw))
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument()
    try:
        document.load(source)
        if document.status() != QPdfDocument.Status.Ready or document.pageCount() < 1:
            raise ValueError("PDF is not ready, is encrypted, or has no pages")
        pages = []
        for page in range(document.pageCount()):
            size = document.pagePointSize(page)
            scale = max_dimension / max(size.width(), size.height())
            image = document.render(page, QSize(max(1, int(size.width() * scale)), max(1, int(size.height() * scale))))
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if image.isNull() or not image.save(buffer, "PNG"):
                raise ValueError(f"Could not render PDF page {page + 1}")
            png = bytes(data)
            asset_id = save_image(page + 1, png) if save_image else None
            pages.append(PageInput(page + 1, document.getAllText(page).text(), png, asset_id))
        return PDFInput(sha256(raw).hexdigest(), tuple(pages))
    finally:
        document.close()
        source.close()
