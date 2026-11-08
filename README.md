
<img width="288" height="48" alt="mascot_spritesheet (2)" src="https://github.com/user-attachments/assets/d5bdfde7-009e-4b10-afed-f182e9fcea1d" />


# Lecture Buddy

Lecture Buddy is a desktop study app we built for FallHacks 2026. It brings lecture slides, a live transcript, explanations, and study notes into one workspace.

Keeping up with a lecture can mean listening, reading slides, and writing notes at the same time. Miss an explanation and the next slide can be hard to follow. Lecture Buddy keeps the material together so you can revisit a concept during class and leave with notes to study from afterward.

## What it does

**Bring in your slides.** Import a PDF or drop it into the workspace. The app displays the original pages and prepares an outline of the lecture, including key terms, definitions, formulas, and diagrams. The Lecture Sections panel follows this outline.

**Follow the spoken lecture.** Record through your microphone and see a transcript as speech comes in. Finalized speech is processed in the background to build section notes. Stopping the recording waits for the remaining transcription before finishing those notes.

**Ask about the material.** Click a slide or section to open its detail screen. You can read a concept breakdown, see related notes and transcript, and ask follow-up questions. Gemini receives the selected material and available lecture context so the conversation stays focused on what you are studying.

**Take the notes with you.** Export PDF prepares a final study document from the transcript, slide material, and section drafts, then saves it as a PDF. It organizes the content, keeps source references, and labels additional examples or incomplete coverage. Re-exporting the same lecture reuses the prepared notes.

## Running it

You need Python 3.10 or later and a Gemini API key. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` if you do not already have one. Add your key and choose models available to your API project. Our demo configuration uses:

```dotenv
GEMINI_KEY=your_api_key
GEMINI_GENERATION_MODEL=gemini-3.8-flash
GEMINI_TRANSCRIPTION_MODEL=gemini-3.5-transcribe-live
```

Chat uses the generation model by default. Set `GEMINI_DEEP_DIVE_MODEL` to choose a separate chat model. Gemini handles slide analysis, transcription, explanations, and note generation, so these features need an internet connection and send lecture material to its API.

Launch the app:

```bash
python -m ui.main
```

Or open a PDF on startup:

```bash
python -m ui.main --pdf 'path/to/lecture.pdf'
```

Import your slides, start recording, and click a slide or lecture section to open its detail screen. When the lecture is over, stop recording, wait for the notes to finish, and click **Export PDF**.

### Microphone setup

On Ubuntu, microphone capture needs PortAudio:

```bash
sudo apt-get install libportaudio2
```

List the input devices the app can see with:

```bash
python test_program.py --list-devices
```

Set `AUDIO_INPUT_DEVICE` in `.env` to a listed device ID or name to choose a specific microphone. WSL needs audio forwarding from Windows; if Linux reports no input devices, the app cannot record until that connection is available.

## Trying it without a microphone

Import a slide deck and wait for its sections to appear. Click **Import Transcript** and select `examples/demo_transcript.txt`, or use your own UTF-8 text file with blank lines between speech chunks.

The included example moves from classification to clustering and ends with a qualification about similarity measures. It is sample speech for testing. A transcript that matches your own slides gives a better picture of how the app connects spoken explanations to the deck.

Session files are saved under `test_outputs/ui-<session>/`. This includes the transcript, slide index, section drafts, and final notes in Markdown and JSON. The PDF goes wherever you choose in the export dialog. Final note content is saved separately, so a PDF write failure does not discard it.

## How it is built

The application is written in Python. PySide6 provides the desktop interface, PDF rendering, and export. Google Gemini handles model requests, `sounddevice` captures microphone audio, and Pydantic validates structured responses and source references. Recording and model requests run in background workers.

- `ui/` contains the workspace, detail screen, and application connections.
- `live_recording/` handles microphone capture and streaming transcription.
- `interpretation/` prepares slides, processes transcript sections, and generates notes.
- `slide_deep_dive/` handles concept explanations and contextual chat.

Run the automated tests without a microphone or API key:

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

For a command-line test with real Gemini requests:

```bash
python -m interpretation.smoke_test --pdf 'path/to/lecture.pdf' --transcript examples/demo_transcript.txt
```

This is a hackathon build. Saved lecture files are available on disk, but the UI does not reopen previous sessions yet. Chat history stays in the current detail screen, and exported equations use readable formula text rather than full mathematical typesetting. Generated explanations and notes should still be checked against the course material.
