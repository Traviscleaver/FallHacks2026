# Lecture Buddy — FallHacks2026

Run the integrated Part C UI from the repository root:

```bash
.venv/bin/python -m ui.main
```

Or open your slides immediately:

```bash
.venv/bin/python -m ui.main --pdf 'Introduction (2).pdf'
```

The app reads `GEMINI_KEY` and `GEMINI_GENERATION_MODEL` from `.env`.
Use the project virtual environment with `google-genai`, `pydantic`,
`python-dotenv` and PySide6 installed. The UI now uses native Qt PDF extraction;
it does not require PyMuPDF or Pillow.

1. Click **Import PDF**, or drop a PDF into the slide panel. Slides appear while
   Gemini prepares their topics. Progress appears in the status bar.
2. Click a topic or slide to inspect its actual summary, definitions, formulas,
   linked notes and related transcript. Main's deep-dive service generates a
   concept breakdown and answers questions in the chat panel. Return with **Back**.
3. Click **Start Recording** to stream microphone speech into the live transcript.
   Interim words are temporary; finalized speech is sent to Part C for notes.
   Click **Stop Recording** to flush trailing speech and finish section notes.
   Alternatively, use **Import Transcript** with a UTF-8 text file whose chunks
   are separated by blank lines, such as `examples/demo_transcript.txt`.
4. The app processes paragraphs in order and generates section notes. The
   **Lecture Sections** panel keeps the outline prepared from your lecture slides,
   with section summaries and page references. Transcript discussions do not
   replace those rows. Click a section to inspect its material and any related notes.
5. Click **Export PDF** once notes exist. This exports the current section notes
   with plain formula source text; it is not lecture-wide final synthesis or
   typeset MathText output.

Slide indexes and completed transcript-test notes are saved under
`test_outputs/ui-<session>/`. These are demo artifacts, not a database/reopen flow.
Import the PDF again to start another transcript test. Real Gemini requests incur
API usage. Closing during processing waits for the background operation to finish.

The chat implementation from main is connected to selected slides, topics and
sections. Original slide images/text and related imported transcript are passed
to it. Background note updates preserve the current conversation; use **Refresh**
to regenerate the concept breakdown. Chat defaults to `GEMINI_GENERATION_MODEL`;
set `GEMINI_DEEP_DIVE_MODEL` to choose a separate model.

Recording uses main's existing `LiveRecordingManager` and `GeminiTranscriber`.
The generation and transcription models are configured separately:
`GEMINI_TRANSCRIPTION_MODEL` defaults to `gemini-3.5-transcribe-live`.
On Ubuntu/WSL, install the native microphone dependency if needed:

```bash
sudo apt-get install libportaudio2
.venv/bin/python test_program.py --list-devices
```

Set optional `AUDIO_INPUT_DEVICE` in `.env` to select a listed microphone ID/name.
The operating system must expose an input device to the process (including WSL
audio support when running under WSL). Errors appear in the transcript/status bar.
The standalone microphone test remains available as `test_program.py`.
Completed recording transcripts are saved as `transcript.txt` alongside the notes.

Run the automated tests without credentials or microphone access:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

See [Part C integration details](interpretation/README.md) for service interfaces.
