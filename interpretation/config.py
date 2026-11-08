from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    model: str = "gemini-2.5-flash"
    analysis_seconds: float = 15
    provisional_seconds: float = 90
    batch_segments: int = 20
    text_budget: int = 24000
    slide_batch_pages: int = 6
    slide_batch_bytes: int = 12_000_000
    retries: int = 2
    timeout_seconds: float = 60

    def __post_init__(self):
        if not self.model or min(self.analysis_seconds, self.provisional_seconds,
                                 self.batch_segments, self.text_budget,
                                 self.slide_batch_pages, self.slide_batch_bytes,
                                 self.timeout_seconds) <= 0 or self.retries < 0:
            raise ValueError("Part C settings must be positive (retries may be zero)")

    @classmethod
    def from_env(cls, path=None):
        # Never override credentials already supplied by the launching application.
        load_dotenv(path or Path(__file__).resolve().parents[1] / ".env", override=False)
        return cls(
            model=os.getenv("GEMINI_GENERATION_MODEL", "gemini-2.5-flash"),
            analysis_seconds=float(os.getenv("C_ANALYSIS_SECONDS", "15")),
            provisional_seconds=float(os.getenv("C_PROVISIONAL_SECONDS", "90")),
            batch_segments=int(os.getenv("C_BATCH_SEGMENTS", "20")),
            text_budget=int(os.getenv("C_TEXT_BUDGET", "24000")),
            slide_batch_pages=int(os.getenv("C_SLIDE_BATCH_PAGES", "6")),
            slide_batch_bytes=int(os.getenv("C_SLIDE_BATCH_BYTES", "12000000")),
            retries=int(os.getenv("C_API_RETRIES", "2")),
            timeout_seconds=float(os.getenv("C_API_TIMEOUT_SECONDS", "60")),
        )
