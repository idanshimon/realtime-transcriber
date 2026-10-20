"""Regression: fast file input must drain every buffered chunk at EOF."""
import queue
import threading
import numpy as np
from chunked_backends import ChunkedHTTPBackend, process_chunked_backend

class RecordingBackend(ChunkedHTTPBackend):
    def __init__(self):
        super().__init__(sample_rate=100, chunk_seconds=1)
        self.requests = []
    def _transcribe_chunk(self, wav_bytes):
        self.requests.append(wav_bytes)
        return [f'chunk {len(self.requests)}']

def test_fast_file_eof_drains_all_full_chunks_and_partial():
    backend = RecordingBackend()
    audio = queue.Queue()
    audio.put(np.ones(1850, dtype=np.float32) * 0.1)
    stop = threading.Event()
    stop.set()
    emitted = []
    process_chunked_backend(backend, audio, stop, threading.Event(), emitted.append)
    assert len(backend.requests) == 19
    assert backend._buffer.size == 0
    assert len(emitted) == 19
