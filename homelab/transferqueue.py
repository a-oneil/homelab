"""Transfer Queue — background batched transfers instead of one at a time."""

import os
import threading
from homelab.transport import get_host, rsync_transfer
from homelab.ui import C, pick_option, prompt_text, success, error, warn


_QUEUE = []
_QUEUE_LOCK = threading.Lock()
_WORKER_THREAD = None
_WORKER_STOP = threading.Event()
_COMPLETED = []


def transfer_queue_menu():
    """Manage the background transfer queue."""
    while True:
        running = _WORKER_THREAD is not None and _WORKER_THREAD.is_alive()

        with _QUEUE_LOCK:
            pending = list(_QUEUE)

        print(f"\n  {C.BOLD}Transfer Queue{C.RESET}")
        print(f"  Worker: {C.GREEN}Running{C.RESET}" if running else f"  Worker: {C.DIM}Stopped{C.RESET}")
        print(f"  Pending: {len(pending)}  Completed: {len(_COMPLETED)}")

        if pending:
            print(f"\n  {C.BOLD}Queued:{C.RESET}")
            for i, item in enumerate(pending):
                src = os.path.basename(item["source"])
                print(f"    {i + 1}. {src} → {item['dest']}")

        if _COMPLETED:
            print(f"\n  {C.BOLD}Recent:{C.RESET}")
            for item in _COMPLETED[-5:]:
                src = os.path.basename(item["source"])
                status = f"{C.GREEN}OK{C.RESET}" if item["success"] else f"{C.RED}FAIL{C.RESET}"
                print(f"    {status} {src}")

        choices = ["+ Add to queue"]
        if running:
            choices.append("Stop worker")
        else:
            choices.append("Start worker")
        choices.extend(["Clear queue", "Clear history", "← Back"])

        idx = pick_option("", choices)
        choice = choices[idx]

        if choice == "← Back":
            return
        elif choice == "+ Add to queue":
            _add_to_queue()
        elif choice == "Start worker":
            _start_worker()
        elif choice == "Stop worker":
            _stop_worker()
        elif choice == "Clear queue":
            with _QUEUE_LOCK:
                count = len(_QUEUE)
                _QUEUE.clear()
            success(f"Cleared {count} items from queue.")
        elif choice == "Clear history":
            _COMPLETED.clear()
            success("History cleared.")


def enqueue(source, dest, is_dir=False):
    """Add a transfer to the queue. Can be called from other modules."""
    with _QUEUE_LOCK:
        _QUEUE.append({"source": source, "dest": dest, "is_dir": is_dir})
    # Auto-start worker if not running
    if _WORKER_THREAD is None or not _WORKER_THREAD.is_alive():
        _start_worker()


def _add_to_queue():
    """Interactively add a local file/folder to the transfer queue."""
    source = prompt_text("Local file or folder path:")
    if not source:
        return
    source = os.path.expanduser(source)
    if not os.path.exists(source):
        error("Path does not exist.")
        return

    dest = prompt_text("Remote destination (e.g. /mnt/user/incoming):")
    if not dest:
        return

    is_dir = os.path.isdir(source)
    dest_spec = f"{get_host()}:{dest}/"
    enqueue(source, dest_spec, is_dir=is_dir)
    success(f"Queued: {os.path.basename(source)}")


def _start_worker():
    """Start the background queue worker."""
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        warn("Worker is already running.")
        return

    _WORKER_STOP.clear()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, daemon=True)
    _WORKER_THREAD.start()
    success("Queue worker started.")


def _stop_worker():
    """Stop the background queue worker."""
    _WORKER_STOP.set()
    success("Queue worker stopping...")


def _worker_loop():
    """Process queued transfers one at a time."""
    while not _WORKER_STOP.is_set():
        item = None
        with _QUEUE_LOCK:
            if _QUEUE:
                item = _QUEUE.pop(0)

        if item is None:
            _WORKER_STOP.wait(timeout=5)
            continue

        try:
            result = rsync_transfer(item["source"], item["dest"], is_dir=item.get("is_dir", False))
            item["success"] = result.returncode == 0
            if result.returncode == 0:
                try:
                    from homelab.notifications import notify
                    notify("Homelab", f"Transfer complete: {os.path.basename(item['source'])}")
                except Exception:
                    pass
        except Exception:
            item["success"] = False

        _COMPLETED.append(item)
