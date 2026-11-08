"""B implements this port with serialized storage operations, never a shared connection."""
import threading
from typing import Protocol
from .models import LectureState


class StaleSnapshot(RuntimeError):
    pass


class InterpretationRepository(Protocol):
    def load(self, lecture_id: str) -> LectureState:
        """Return an independent snapshot, including all finalized speech known to B."""
        ...

    def commit(self, state: LectureState, expected_revision: int) -> None:
        """Atomically compare revision and save; raise StaleSnapshot on conflict.

        Advance revision by one. Persist derived records/cursor together. This
        method must marshal to B's storage worker or use its own connection.
        """
        ...


class MemoryRepository:
    """Thread-safe development adapter only; deliberately not a second storage layer."""
    def __init__(self):
        self._lock = threading.RLock()
        self._states = {}

    def load(self, lecture_id):
        with self._lock:
            return self._states.get(lecture_id, LectureState(lecture_id=lecture_id)).model_copy(deep=True)

    def commit(self, state, expected_revision):
        with self._lock:
            current = self._states.get(state.lecture_id)
            if (current.revision if current else 0) != expected_revision:
                raise StaleSnapshot("Inputs changed; retry from current snapshot")
            saved = state.model_copy(deep=True)
            saved.revision = expected_revision + 1
            self._states[state.lecture_id] = saved
