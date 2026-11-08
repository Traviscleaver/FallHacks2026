"""Run with: python3 -m interpretation.smoke_test --pdf /path/to/slides.pdf."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import time

from .config import Settings
from .gemini_adapter import GeminiAdapter, safe_error_message
from .incremental_notes import IncrementalNotes
from .models import Segment
from .pdf_input import extract_pdf
from .repository import MemoryRepository
from .slide_preparation import SlidePreparation


class RecordedCalls:
    """Keep timings/error codes for diagnosis, without logging keys or SDK error bodies."""
    def __init__(self, ai):
        self.ai = ai
        self.calls = []

    def generate(self, schema, instruction, payload, images=()):
        started = time.monotonic()
        call = {"operation": schema.__name__, "status": "running"}
        self.calls.append(call)
        try:
            result = self.ai.generate(schema, instruction, payload, images)
            call["status"] = "ok"
            return result
        except Exception as exc:
            call.update(status="failed", error_type=type(exc).__name__,
                        api_status=getattr(exc, "code", None), message=safe_error_message(exc))
            print(f"{schema.__name__} failed ({call['api_status'] or call['error_type']}): {call['message']}", flush=True)
            raise
        finally:
            call["seconds"] = round(time.monotonic() - started, 2)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def run_smoke(pdf, output, transcript=None, *, ai=None, settings=None):
    """CLI test harness only: writes review artifacts, not application repository data."""
    settings = settings or Settings.from_env()
    paragraphs = []
    if transcript:
        import re
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", Path(transcript).read_text(encoding="utf-8")) if p.strip()]
        if not paragraphs:
            raise ValueError("Transcript file needs at least one nonempty paragraph")
    output = Path(output)
    # A unique directory protects earlier reports; the CLI chooses a timestamp by default.
    output.mkdir(parents=True, exist_ok=False)
    assets = output / "pages"
    assets.mkdir()
    owned_ai = ai is None
    repository = MemoryRepository()
    tracked = None
    lecture_id = None
    events = []
    try:
        if ai is None:
            ai = GeminiAdapter(settings)
        tracked = RecordedCalls(ai)

        def save_page(page, png):
            path = assets / f"page-{page:03d}.png"
            path.write_bytes(png)
            return str(path.resolve())

        source = extract_pdf(pdf, save_image=save_page)
        lecture_id = "smoke-" + source.pdf_hash[:16]
        print(f"Read {len(source.pages)} PDF pages. Model: {settings.model}", flush=True)
        slides = SlidePreparation(repository, tracked, settings)
        index = slides.prepare_slides(lecture_id, source,
            progress=lambda lid, done, total: print(f"Slide preparation: {done}/{total} ready", flush=True))
        write_json(output / "slide_index.json", index.model_dump(mode="json"))
        if index.status != "ready":
            print(f"Index status: {index.status}. Inspect slide_index.json and ai_calls.json.", flush=True)
            return 1
        if paragraphs:
            notes = IncrementalNotes(repository, tracked, settings)
            for i, text in enumerate(paragraphs):
                notes.accept_final_segment(lecture_id, Segment(id=f"paragraph-{i + 1}",
                    lecture_id=lecture_id, sequence=i, text=text, timing_quality="unknown"))
                notes.process_finalized_speech(lecture_id)
                notes.generate_due_notes(lecture_id)
                state = repository.load(lecture_id)
                events.append({"after_paragraph": i + 1,
                    "sections": [s.model_dump(mode="json") for s in state.sections],
                    "latest_decision": state.alignments[-1].model_dump(mode="json") if state.alignments else None})
                print(f"Analyzed paragraph {i + 1}/{len(paragraphs)}: "
                      f"{len(state.sections)} section(s), latest {state.sections[-1].status}", flush=True)
            # The text file is the entire supplied test transcript; no audio flush is involved.
            state = notes.finish_lecture(lecture_id, transcript_complete=True)
            current = [n for n in state.notes if not n.stale or n.user_edited]
            markdown = []
            for section in state.sections:
                markdown.append(f"## {section.title}\n")
                for block in current:
                    if block.section_id == section.id:
                        markdown.append(block.markdown + "\n")
                        markdown.extend(f"> {warning}\n" for warning in block.coverage_warnings)
            (output / "notes.md").write_text("\n".join(markdown), encoding="utf-8")
        print(f"Results saved to {output.resolve()}", flush=True)
        return 0
    except Exception as exc:
        message = "Test failed; inspect ai_calls.json and state.json. No key or API error body was logged."
        if isinstance(exc, ModuleNotFoundError):
            message = (f"Missing Python module: {exc.name}. Activate the project environment "
                       "and install google-genai, pydantic and python-dotenv using python -m pip.")
        write_json(output / "error.json", {"error_type": type(exc).__name__,
            "api_status": getattr(exc, "code", None),
            "message": message})
        if isinstance(exc, ModuleNotFoundError):
            print(message, flush=True)
        print(f"Test failed ({type(exc).__name__}); diagnostics: {output.resolve()}", flush=True)
        return 1
    finally:
        if tracked is not None:
            write_json(output / "ai_calls.json", tracked.calls)
        write_json(output / "events.json", events)
        # Save partial work as well as successful results.
        if lecture_id is not None:
            write_json(output / "state.json", repository.load(lecture_id).model_dump(mode="json"))
        if owned_ai and ai is not None:
            ai.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Test Part C with a local lecture PDF and optional transcript. Makes real Gemini calls.")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--transcript", type=Path, help="UTF-8 text; separate finalized speech chunks with blank lines")
    parser.add_argument("--output", type=Path, help="New output directory; defaults to test_outputs/<timestamp>")
    args = parser.parse_args(argv)
    for path in (args.pdf, args.transcript):
        if path is not None and not path.is_file():
            parser.error(f"File does not exist: {path}")
    output = args.output or Path("test_outputs") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    if output.exists():
        parser.error("Output directory already exists; choose a new directory")
    # This command has no interactive UI. PDF objects and network calls stay in the worker.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])
    settings = Settings.from_env()
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(run_smoke, args.pdf, output, args.transcript, settings=settings).result()
    app.processEvents()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
