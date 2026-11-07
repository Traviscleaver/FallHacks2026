# gemini_stub.py

class GeminiServiceStub:
    def __init__(self):
        self.api_key = "YOUR_GEMINI_API_KEY"

    def start_recording(self):
        print("[GeminiStub] Recording started.")

    def stop_recording(self):
        print("[GeminiStub] Recording stopped.")

    def import_slides(self, path):
        print(f"[GeminiStub] Slides imported: {path}")

    def request_im_lost(self, slide):
        print(f"[GeminiStub] 'I'm Lost' requested on slide {slide}")

    def ask_chat(self, q, ctx):
        return f"Based on {ctx}: The weight matrix W scales the inputs before activation."

    def export_pdf(self, path):
        print(f"[GeminiStub] Exported PDF to {path}")