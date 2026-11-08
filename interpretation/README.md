# Part C: slide preparation and incremental notes

The existing desktop UI is now wired to these services. Launch it with
`.venv/bin/python -m ui.main`, import a PDF, then optionally import a transcript
text file to demonstrate notes. See the [root README](../README.md) for steps.
Main's microphone/live-transcription service is connected to the transcript UI
and forwards finalized speech to Part C; stopping flushes remaining speech before
finishing section notes. Main's deep-dive/chat service is
connected through an adapter supplying original slide images/text, related
transcript and section notes. **Export PDF** now prepares final study notes from
the original sources and section drafts before exporting. Final content is cached
for unchanged input and saved independently of the PDF. MathText rendering is
outside this MVP integration.

Implemented services use Python, Pydantic v2, the `google-genai` SDK and native
PySide6 signals. They do not create another app shell, recording service or database.

## Configuration

The repository root `.env` was created with a blank `GEMINI_KEY`. Add the key
there. This is the same variable used by `live_recording.GeminiTranscriber`.
`.env` is ignored by Git; `.env.example` documents all supported settings.
Call `Settings.from_env()` before constructing either Gemini adapter so B also
sees the loaded key. Existing process environment values take precedence.

Install dependencies using the project's Python environment:

```sh
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

`GEMINI_GENERATION_MODEL` selects the generation model. The code default is
`gemini-2.5-flash`; this project's local `.env` now selects `gemini-3.8-flash`,
verified with the configured API key. The API rejected 2.5 Flash for new users.
Change the setting if unavailable to your API project. No API call is
made at import time. No embedding model/key is required: this implementation uses
the full compact outline for small decks and keyword candidates for larger decks.

## Test with your own slides

Start with a small 3–6-page PDF that includes a formula or diagram. The following
command makes real Gemini requests using `.env`; no microphone or app UI is needed:

```sh
python3 -m interpretation.smoke_test --pdf /path/to/lecture.pdf
```

Results are saved under a new `test_outputs/<timestamp>/` directory (ignored by
Git). Compare `slide_index.json` against the originals in `pages/`. Check page
numbers, formulas, symbol meanings, visual descriptions, subsection grouping and
uncertainty flags. `ai_calls.json` records timing and API error codes without keys.
A failed or partial index exits with status 1 and keeps diagnostic/partial output.
Each run starts fresh and can incur new Gemini usage.

To test incremental notes, write a UTF-8 text file with one finalized speech chunk
per paragraph, separated by blank lines. Use two topics: explain a concept and
give two examples, explicitly transition to a second concept, then explain it in
another paragraph to confirm the transition. Include a qualification in the last
paragraph. Use concepts actually present in your PDF.

```sh
python3 -m interpretation.smoke_test --pdf /path/to/lecture.pdf --transcript /path/to/speech.txt
```

Read `notes.md`, then inspect `events.json` to see section states after each chunk.
Check that repeated examples stay together, the transition closes the first
section at its earlier source boundary, and the final paragraph appears in notes.
`state.json` includes complete source references and saved note versions. This
runner sends one analysis request per paragraph to simulate periodic updates; it
does not wait 15 seconds between chunks and does not test microphone capture or
persistent application storage. The test transcript has no measured audio timing.

If a request fails, `ai_calls.json` may show 400 (request/model configuration),
401/403 (credentials/access), 404 (model unavailable), or 429 (quota/rate limits).
Inspect the status rather than posting your `.env` or API key. The output includes
the lecture text and notes, so choose which artifacts you share.

## Integration

```python
from interpretation.config import Settings
from interpretation.gemini_adapter import GeminiAdapter
from interpretation.repository import MemoryRepository
from interpretation.slide_preparation import SlidePreparation
from interpretation.incremental_notes import IncrementalNotes
from interpretation.workers import InterpretationController

settings = Settings.from_env()
ai = GeminiAdapter(settings)
repository = MemoryRepository()  # Replace with B's persistent adapter.
slides = SlidePreparation(repository, ai, settings)
notes = IncrementalNotes(repository, ai, settings)

# Create on the GUI thread. Pass the existing LiveRecordingManager instance.
controller = InterpretationController(slides, notes, recording_manager)
controller.section_updated.connect(show_section)
controller.notes_updated.connect(show_notes)
controller.processing_error.connect(show_processing_error)
```

Create `PDFInput(pdf_hash, pages)` from A's import workflow, with every actual PDF
page in one-based order and each page's extracted text, PNG and optional saved
image asset ID. Hash the exact local PDF bytes using SHA-256. Blank text is valid;
the original image remains required. `pdf_input.extract_pdf(approved_path,
save_image=...)` is an optional helper: run it in a worker with an existing
`QGuiApplication`. It creates its own thread-local Qt PDF objects. It does not
touch A's viewer. `save_image(page_number, png)` should call B's asset service and
return the saved asset ID. The helper currently holds rendered pages in memory;
for very large decks A should provide a streamed asset-backed handoff instead.

Pass the immutable result to `controller.prepare_slides(lecture_id, source)` on
the GUI thread. Model calls run on one bounded executor worker. Slide index
results contain stable slide IDs, per-page statuses, subsection relationships,
original asset references, searchable text and a glossary B can use for hints.
`slides_ready` carries the result even if it is partial/failed; inspect `status`.
Call preparation again with the same source to retry failed units; successful
batches and IDs are retained. A changed PDF creates new page identities.

Final segment and gap signals are connected automatically. Interim speech is
never consumed. A timer coalesces new speech into ordered batches. A bounded
batch can report a candidate transition, then a later batch confirms the earlier
boundary. Inside-segment boundaries use Python string offsets without rewriting
B's segment. Open sections contain usable source memberships before notes exist.
Section notes are generated after confirmed closure; sufficiently long open
sections receive provisional checkpoints. Alignments retain raw decisions
separately from the accepted page estimate, with successive-decision confirmation
for weaker switches. No viewer navigation occurs.

On reopening, B's repository must hydrate saved segments, indexes, section state,
note versions, gaps and cursor before `controller.resume(lecture_id)`. On End
Lecture, wait until B has saved all final speech, then call:

```python
controller.finalize(lecture_id, transcript_complete=True)  # False on incomplete flush.
# Wait for final_sections_ready before later final synthesis or shutdown.
```

Do not infer successful flush from `status_changed(..., "stopped")`: the current
recording manager also reports stopped after a timeout. The transcriber now
reports flush timeouts, and the UI facade tracks errors to mark incomplete
recordings. The current recording manager emits `transcript_final` even
after a save failure. The application/B must resolve storage failures and track
completion explicitly. This adapter does not silently treat STOPPED as complete.
It preserves supplied gaps and attaches deterministic coverage warnings to notes.

Use `controller.shutdown()` on the GUI thread once final work finishes; keep the
QObject alive until active jobs finish. Close `ai` afterward. An application
shutdown coordinator may use `shutdown(wait=True)` outside an interactive GUI
callback. Cancelling interpretation never stops recording.

## Repository contract for B

`InterpretationRepository.load(lecture_id)` returns a detached `LectureState`
snapshot, including saved finalized speech. `commit(state, expected_revision)`
atomically compares the revision, persists derived records and cursor, and
increments revision. Raise `StaleSnapshot` on conflict. B's adapter may map the
logical state into normalized tables; it need not store one JSON blob. Marshal
these calls to B's serialized storage worker or use per-thread connections with
serialized writes. Never share a SQLite connection with generation workers.

Every source change must advance the snapshot revision. Existing B segments have
no `text_revision`; their adapter defaults to revision 1. Corrections must keep
the segment ID/sequence and increment the text revision. Conservative replay
invalidates derived relationships and generated notes while preserving old note
versions and user-edited blocks. Retries are generation-key idempotent. Any
source change while Gemini is working rejects the stale completion.

`MemoryRepository` is a thread-safe development/fixture adapter, **not persistent
storage**. No B-owned repository implementation exists in the current checkout.
The UI should show current blocks (`not stale`) plus preserved `user_edited`
blocks, and label `provisional`, coverage warnings and analysis backlog. User
edits must be persisted through B with a revision increment.

## Current boundaries

- One topic transition per analysis batch is supported. Reduce
  `C_BATCH_SEGMENTS` for rapidly changing discussions. This is a demo MVP rather
  than a tuned multi-boundary segmentation algorithm.
- A single transport segment larger than `C_TEXT_BUDGET` yields a recoverable
  error; increase the budget rather than silently skipping its speech. Long
  section notes are generated from chunks of original spans.
- Formulas and diagrams are indexed from original visual inputs. Live alignment
  currently uses that metadata; visual re-verification and embeddings remain
  future matching improvements. Cross-batch grouping merges equal-title concepts
  sharing pages; manual fixture review is still needed for ambiguous grouping.
- Source references and origins are validated; factual interpretation still needs
  review against a real lecture. Visual slide extraction has been tested with an
  authenticated Gemini request; live transcript/notes behavior still needs review
  against a real lecture transcript.
- The UI shows directly related slide material, section notes and transcript,
  includes main's contextual chat and concept breakdown, and synthesizes final
  study notes when Export PDF is selected. Structured chat citations/persistence,
  selection-offset mapping and typeset equation rendering remain unimplemented.
  `finish_lecture` closes/generates section drafts; `FinalNotesService.finalize`
  prepares the final document using original evidence and validates its sources.

Implementation follows the official
[structured-output](https://ai.google.dev/gemini-api/docs/structured-output) and
[SDK](https://googleapis.github.io/python-genai/) interfaces. Tests exercise a
fake AI plus real Qt PDF rendering without credentials or microphone access.

The adapter sends a compatible JSON Schema through `response_json_schema`.
Length limits, list-size limits and rejection of extra fields remain enforced by
Pydantic after generation; those constraints are omitted from the wire schema
because the live endpoint rejected them. Test logs show redacted API error
messages and status codes, rather than SDK request objects or credentials.
