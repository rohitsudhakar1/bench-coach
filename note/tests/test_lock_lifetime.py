"""Finished runs must not retain locks; concurrent rebuilds still serialize."""
import gc
import threading
from concurrent.futures import ThreadPoolExecutor

from note import service


def test_completed_run_locks_do_not_accumulate(tmp_path):
    baseline = len(service._locks)
    for i in range(1000):
        lock = service._build_lock(tmp_path / str(i))
        with lock:
            assert service._build_lock(tmp_path / str(i)) is lock
    del lock
    gc.collect()
    assert len(service._locks) == baseline


def test_waiting_builder_keeps_same_lock_alive(tmp_path):
    path = tmp_path / 'run'
    first = service._build_lock(path)
    first.acquire()
    waiting = threading.Event()
    acquired = threading.Event()

    def rebuild():
        lock = service._build_lock(path)
        waiting.set()
        with lock:
            acquired.set()
            assert service._build_lock(path) is lock

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rebuild)
        try:
            assert waiting.wait(2)
            assert not acquired.wait(.05)
            assert service._build_lock(path) is first
        finally:
            first.release()
            del first
        future.result(timeout=2)
    gc.collect()
    assert str(path.resolve()) not in service._locks
