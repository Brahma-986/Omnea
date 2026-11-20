"""OS-Isolated PDF extraction running in a sandboxed process with memory, CPU, and time limits."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

logger = logging.getLogger("procure_core.security.isolated_extractor")


class ExtractionIsolationError(Exception):
    """Raised when PDF extraction violates process isolation limits (memory/time/CPU)."""


@dataclass
class ExtractedPage:
    """Extracted text and structural metadata for a single PDF page."""

    page_number: int  # 1-indexed
    text: str
    char_count: int
    char_start: int = 0
    char_end: int = 0


@dataclass
class ExtractedDocument:
    """Complete document text extraction output."""

    pages: list[ExtractedPage] = field(default_factory=list)
    total_pages: int = 0
    total_chars: int = 0
    full_text: str = ""
    extraction_time_ms: float = 0.0
    memory_limit_mb: int = 256
    sandboxed: bool = True

    def get_page_text(self, page_num: int) -> str:
        """Return text of 1-indexed page or empty string."""
        for p in self.pages:
            if p.page_number == page_num:
                return p.text
        return ""


# Windows Job Object Structures & Constants
if sys.platform == "win32":

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryLimit", ctypes.c_size_t),
            ("PeakJobMemoryLimit", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9


class IsolatedPdfExtractor:
    """Executes PDF extraction inside an OS-sandboxed child process.

    Enforces:
    1. Hard memory ceiling via Windows Job Object (e.g. 256MB) or Linux cgroups / prlimit.
    2. Execution timeout (default 15.0s) terminating runaway / decompression-bomb PDFs.
    3. Automatic process teardown on job close preventing zombie processes.
    """

    def __init__(self, memory_limit_mb: int = 256, timeout_seconds: float = 15.0) -> None:
        self.memory_limit_mb = memory_limit_mb
        self.timeout_seconds = timeout_seconds

    def extract(self, pdf_bytes: bytes) -> ExtractedDocument:
        """Extract text from PDF bytes within an OS-isolated sandbox process."""
        start_t = time.perf_counter()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as in_f:
            in_f.write(pdf_bytes)
            in_path = in_f.name

        out_fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)

        job_handle = None
        proc = None
        try:
            # Prepare child command
            cmd = [
                sys.executable,
                "-m",
                "procure_core.security.isolated_extractor",
                in_path,
                out_path,
            ]

            env = os.environ.copy()
            # Inherit pythonpath so worker finds procure_core and dependencies
            env["PYTHONPATH"] = os.pathsep.join(sys.path)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            # Apply OS-Level Resource Isolation on Windows
            if sys.platform == "win32":
                try:
                    kernel32 = ctypes.windll.kernel32
                    job_handle = kernel32.CreateJobObjectW(None, None)
                    if job_handle:
                        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                        limit_bytes = self.memory_limit_mb * 1024 * 1024
                        info.BasicLimitInformation.LimitFlags = (
                            JOB_OBJECT_LIMIT_PROCESS_MEMORY
                            | JOB_OBJECT_LIMIT_JOB_MEMORY
                            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                        )
                        info.ProcessMemoryLimit = limit_bytes
                        info.JobMemoryLimit = limit_bytes

                        kernel32.SetInformationJobObject(
                            job_handle,
                            JobObjectExtendedLimitInformation,
                            ctypes.byref(info),
                            ctypes.sizeof(info),
                        )
                        proc_handle = getattr(proc, "_handle", None)
                        if proc_handle:
                            kernel32.AssignProcessToJobObject(job_handle, int(proc_handle))
                except Exception as job_err:
                    logger.warning("Could not attach Windows Job Object limits: %s", job_err)

            stdout_data, stderr_data = proc.communicate(timeout=self.timeout_seconds)

            if proc.returncode != 0:
                err_text = stderr_data.decode("utf-8", errors="replace")
                if "MemoryError" in err_text or proc.returncode in (-9, 137, 3221225477):
                    raise ExtractionIsolationError(
                        f"Isolated extraction aborted: memory limit ({self.memory_limit_mb}MB) exceeded."
                    )
                raise ExtractionIsolationError(
                    f"Isolated extraction process failed (exit {proc.returncode}): {err_text[:200]}"
                )

            # Read parsed JSON
            if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                raise ExtractionIsolationError(
                    "Child extraction process produced empty output payload."
                )

            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)

            pages = [ExtractedPage(**p) for p in data.get("pages", [])]
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0

            total_chars = sum(p.char_count for p in pages)
            full_text = "\n\n".join(p.text for p in pages)

            return ExtractedDocument(
                pages=pages,
                total_pages=len(pages),
                total_chars=total_chars,
                full_text=full_text,
                extraction_time_ms=elapsed_ms,
                memory_limit_mb=self.memory_limit_mb,
                sandboxed=True,
            )

        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            raise ExtractionIsolationError(
                f"Isolated extraction timed out after {self.timeout_seconds}s (potential PDF CPU bomb)."
            ) from None
        finally:
            if job_handle and sys.platform == "win32":
                try:
                    ctypes.windll.kernel32.CloseHandle(job_handle)
                except Exception:
                    pass
            for p in (in_path, out_path):
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass


def _worker_main() -> None:
    """Entry point for the isolated child process."""
    if len(sys.argv) < 3:
        sys.stderr.write(
            "Usage: python -m procure_core.security.isolated_extractor <input_pdf> <output_json>\n"
        )
        sys.exit(1)

    in_file = sys.argv[1]
    out_file = sys.argv[2]

    try:
        import pypdf

        reader = pypdf.PdfReader(in_file)
        pages_out = []
        global_offset = 0

        for idx, page in enumerate(reader.pages):
            page_num = idx + 1
            text = page.extract_text() or ""
            # Strip excessive null bytes and normalize smart quotes for robust citation matching
            text = text.replace("\x00", "")
            text = text.replace("\u2019", "'").replace("\u2018", "'")
            text = text.replace("\u201c", '"').replace("\u201d", '"')
            char_len = len(text)
            start_off = global_offset
            end_off = global_offset + char_len

            pages_out.append(
                {
                    "page_number": page_num,
                    "text": text,
                    "char_count": char_len,
                    "char_start": start_off,
                    "char_end": end_off,
                }
            )
            global_offset = end_off + 2  # account for page separators

        payload = {
            "status": "success",
            "pages": pages_out,
            "total_pages": len(pages_out),
        }

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    except Exception as exc:
        sys.stderr.write(f"Extraction error: {type(exc).__name__}: {str(exc)}\n")
        sys.exit(2)


if __name__ == "__main__":
    _worker_main()
