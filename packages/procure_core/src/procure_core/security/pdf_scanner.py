"""Structural PDF security scanner for detecting hostile constructs, hex escapes, and stream exploits."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PdfScanResult:
    """Result of structural PDF security analysis."""

    is_safe: bool
    threats: list[str] = field(default_factory=list)
    raw_threat_count: int = 0
    detected_constructs: list[str] = field(default_factory=list)


class StructuralPdfScanner:
    """Zero-trust structural scanner analyzing raw PDF bytes before parsing."""

    # High-risk PDF actions and dictionary keys
    MALICIOUS_ACTIONS = [
        "javascript",
        "js",
        "launch",
        "submitform",
        "importdata",
    ]

    # Embedded payload patterns
    SUSPICIOUS_EMBEDDINGS = [
        "embeddedfiles",
        "embeddedfile",
    ]

    @classmethod
    def normalize_hex_escapes(cls, raw_bytes: bytes) -> str:
        """Decode hex-escaped name tokens (e.g., /J#61vaScript -> /JavaScript).

        In PDF syntax, characters in names may be encoded as # followed by two hex digits.
        """
        # First decode ascii-safely with replacement
        raw_text = raw_bytes.decode("latin1", errors="replace")

        # Replace #XX with the corresponding character
        def _replace_hex(match: re.Match[str]) -> str:
            hex_val = match.group(1)
            try:
                char_code = int(hex_val, 16)
                return chr(char_code)
            except Exception:
                return match.group(0)

        normalized = re.sub(r"#([0-9a-fA-F]{2})", _replace_hex, raw_text)
        return normalized

    @classmethod
    def scan(cls, raw_bytes: bytes) -> PdfScanResult:
        """Perform zero-trust structural inspection on raw PDF bytes."""
        if not raw_bytes:
            return PdfScanResult(is_safe=False, threats=["Empty file or zero-length payload."])

        # Verify PDF header magic bytes
        if not raw_bytes.startswith(b"%PDF-"):
            # Check within first 1024 bytes (some PDFs have leading BOM or space)
            if b"%PDF-" not in raw_bytes[:1024]:
                return PdfScanResult(
                    is_safe=False,
                    threats=["Invalid PDF header: Missing '%PDF-' magic bytes."],
                )

        threats: list[str] = []
        constructs: list[str] = []

        # 1. Normalize hex escapes across the entire document
        normalized_text = cls.normalize_hex_escapes(raw_bytes)
        normalized_lower = normalized_text.lower()

        # Check for hex-escaped name obfuscation evasion attempt
        raw_text = raw_bytes.decode("latin1", errors="replace")
        has_hex_escapes = bool(re.search(r"/[\w]*#[0-9a-fA-F]{2}", raw_text))

        # 2. Check for Malicious Actions (direct or normalized from hex)
        for action in cls.MALICIOUS_ACTIONS:
            pattern = rf"/{re.escape(action)}\b"
            if re.search(pattern, normalized_lower):
                threat_msg = f"Hostile action dictionary detected: /{action.upper()}"
                if has_hex_escapes and re.search(r"/[\w#]+", raw_text):
                    threat_msg += " (obfuscated via hex-escaped name tokens)"
                threats.append(threat_msg)
                constructs.append(action)

        # 3. Check for Embedded File Payloads (/EmbeddedFiles, /Type /EmbeddedFile)
        for embed_key in cls.SUSPICIOUS_EMBEDDINGS:
            pattern = rf"/{re.escape(embed_key)}\b"
            if re.search(pattern, normalized_lower):
                # Inspect if dangerous file extensions or PE headers are present
                has_exe = bool(re.search(r"\.(exe|dll|bat|ps1|vbs|sh|cmd)\b", normalized_lower))
                has_pe_magic = (
                    b"MZ" in raw_bytes and b"This program cannot be run in DOS mode" in raw_bytes
                )
                if has_exe or has_pe_magic:
                    threats.append(
                        f"Malicious executable payload detected in embedded stream (/{embed_key})"
                    )
                    constructs.append("embedded_executable")
                else:
                    threats.append(f"High-risk embedded file stream detected: /{embed_key}")
                    constructs.append("embedded_file")

        # 4. Check for Object Stream (/ObjStm) Bomb or Malicious Stream Nesting
        obj_stm_matches = list(re.finditer(r"/objstm\b", normalized_lower))
        if len(obj_stm_matches) > 100:
            threats.append(
                f"Excessive object stream fragmentation ({len(obj_stm_matches)} /ObjStm entries detected: possible compression bomb)"
            )
            constructs.append("excessive_objstm")

        # Check for recursive stream compression (e.g. multiple /FlateDecode layers)
        multi_flate = re.search(r"/filter\s*\[\s*(/flatedecode\s*){3,}\]", normalized_lower)
        if multi_flate:
            threats.append(
                "Recursive decompression bomb detected: multiple nested /FlateDecode filters"
            )
            constructs.append("compression_bomb")

        # 5. Check for Deeply Nested Dictionaries (parser stack exhaustion exploit)
        max_nesting = 0
        current_nesting = 0
        for char in normalized_text:
            if char == "<":
                current_nesting += 1
                if current_nesting > max_nesting:
                    max_nesting = current_nesting
            elif char == ">":
                if current_nesting > 0:
                    current_nesting -= 1
        if max_nesting > 60:
            threats.append(
                f"Dangerous dictionary nesting depth ({max_nesting} levels > 60 threshold: stack exhaustion risk)"
            )
            constructs.append("stack_exhaustion")

        is_safe = len(threats) == 0
        return PdfScanResult(
            is_safe=is_safe,
            threats=threats,
            raw_threat_count=len(threats),
            detected_constructs=constructs,
        )
