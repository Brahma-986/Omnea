"""PDF test fixture factory for Stage 5 TPRM testing.

Generates real byte-compliant PDFs for clean SOC 2, qualified opinion SOC 2,
and hostile/malicious payloads.
"""

from __future__ import annotations

import io


def build_raw_pdf_bytes(page_texts: list[str], extra_trailer_entries: str = "") -> bytes:
    """Build a standards-compliant multi-page PDF document in raw bytes."""
    catalog_id, font_id, pages_id = 1, 2, 3
    current_id = 4
    page_ids, content_ids = [], []
    for _ in page_texts:
        page_ids.append(current_id)
        content_ids.append(current_id + 1)
        current_id += 2

    objects: dict[int, bytes] = {}
    objects[catalog_id] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin1")
    objects[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    kids_str = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_ids)} >>".encode(
        "latin1"
    )

    for pid, cid, txt in zip(page_ids, content_ids, page_texts):
        safe_txt = txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_data = f"BT\n/F1 12 Tf\n72 720 Td\n({safe_txt}) Tj\nET".encode("latin1")
        objects[pid] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {cid} 0 R >>"
        ).encode("latin1")
        objects[cid] = (
            f"<< /Length {len(stream_data)} >>\nstream\n".encode("latin1")
            + stream_data
            + b"\nendstream"
        )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for oid in sorted(objects.keys()):
        offsets[oid] = out.tell()
        out.write(f"{oid} 0 obj\n".encode("latin1"))
        out.write(objects[oid])
        out.write(b"\nendobj\n")

    startxref = out.tell()
    out.write(b"xref\n")
    out.write(f"0 {len(objects) + 1}\n".encode("latin1"))
    out.write(b"0000000000 65535 f \n")
    for oid in sorted(objects.keys()):
        out.write(f"{offsets[oid]:010d} 00000 n \n".encode("latin1"))
    out.write(b"trailer\n")
    trailer_dict = f"<< /Size {len(objects) + 1} /Root {catalog_id} 0 R {extra_trailer_entries} >>"
    out.write(trailer_dict.encode("latin1") + b"\n")
    out.write(b"startxref\n")
    out.write(f"{startxref}\n".encode("latin1"))
    out.write(b"%%EOF\n")
    return out.getvalue()


def build_clean_soc2_pdf(vendor_name: str = "Datadog, Inc.") -> bytes:
    """Build a realistic, clean SOC 2 Type II PDF covering all 6 mandatory control domains."""
    pages = [
        # Page 1: Auditor Opinion
        (
            f"Independent Service Auditor's Report for {vendor_name}. "
            "Opinion: In our opinion, in all material respects, management's description "
            "of the system fairly presents the infrastructure and controls operated effectively "
            "throughout the review period to achieve the trust services criteria (unqualified opinion)."
        ),
        # Page 2: Access Control
        (
            "Section 3: Access Control and Identity Governance. "
            "Access to production systems is strictly restricted to authorized personnel "
            "and requires mandatory multi-factor authentication (MFA) and single sign-on (SSO). "
            "All administrative access is logged and reviewed quarterly."
        ),
        # Page 3: Encryption
        (
            "Section 4: Cryptographic Protections. "
            "All customer data at rest is encrypted using industry standard AES-256 algorithm. "
            "Data in transit across public networks is encrypted using TLS 1.3 cryptographic protocols. "
            "Key management is secured using hardware security modules (HSMs)."
        ),
        # Page 4: Vulnerability Management
        (
            "Section 5: Vulnerability Management and Penetration Testing. "
            "Automated vulnerability scans are performed on a weekly schedule across all production hosts. "
            "External penetration tests are conducted annually by an independent third party, "
            "and critical vulnerabilities must be remediated within 14 calendar days."
        ),
        # Page 5: Incident Response
        (
            "Section 6: Incident Detection and Response. "
            "A formal incident response plan is tested annually with 24/7 on-call security rotation. "
            "In the event of a confirmed security breach, affected customers will be notified within 24 hours."
        ),
        # Page 6: Data Retention & Privacy
        (
            "Section 7: Data Retention, Disposal and Privacy. "
            "Customer data is logically segregated using tenant isolation boundaries. "
            "Upon contract termination, data is securely erased within 30 days in compliance with GDPR and CCPA."
        ),
    ]
    return build_raw_pdf_bytes(pages)


def build_qualified_soc2_pdf(vendor_name: str = "Vulnerable SaaS LLC") -> bytes:
    """Build a SOC 2 report containing a qualified opinion due to unpatched systems and no MFA."""
    pages = [
        # Page 1: Auditor Opinion
        (
            f"Independent Service Auditor's Report for {vendor_name}. "
            "Basis for Qualified Opinion: As described in section 4.2, privileged credentials lacked MFA. "
            "Qualified Opinion: In our opinion, except for the effects of the matter described in the "
            "Basis for Qualified Opinion paragraph, controls were not operating effectively."
        ),
        # Page 2: Access Control Deficiencies
        (
            "Section 3: Access Controls. "
            "Privileged access was granted without multi-factor authentication on legacy endpoints, "
            "presenting an unmitigated credential stuffing risk."
        ),
        # Page 3: Encryption
        (
            "Section 4: Data Protection. "
            "Customer backups are stored without encryption at rest, using plain disk volumes."
        ),
    ]
    return build_raw_pdf_bytes(pages)


def build_hostile_pdf_javascript() -> bytes:
    """Build a hostile PDF with hex-escaped /#4a#61#76#61#53#63#72#69#70#74 launch construct."""
    # /#4a#61#76#61#53#63#72#69#70#74 decodes to /JavaScript
    malicious_trailer = "/Names << /#4a#61#76#61#53#63#72#69#70#74 << /Names [(payload) << /S /#4a#53 /#4a#53 (app.alert('PWNED')) >>] >> >>"
    return build_raw_pdf_bytes(
        ["Invoice and Security Summary: Please click to review terms."],
        extra_trailer_entries=malicious_trailer,
    )


def build_hostile_pdf_launch() -> bytes:
    """Build a hostile PDF containing a /Launch action to execute an arbitrary command."""
    malicious_action = "/OpenAction << /S /Launch /F (powershell.exe) /P (-Command calc.exe) >>"
    return build_raw_pdf_bytes(
        ["Document requires interactive confirmation."],
        extra_trailer_entries=malicious_action,
    )


def build_hostile_pdf_embedded_exe() -> bytes:
    """Build a hostile PDF containing embedded executable bytes."""
    # PE executable header simulation: 'MZ' and DOS header text
    pe_header = "MZ......................This program cannot be run in DOS mode"
    malicious_embed = f"/EmbeddedFiles << /Names [(malware.exe) << /Type /EmbeddedFile /Params << /Size 1024 >> >>] >> /Payload ({pe_header})"
    return build_raw_pdf_bytes(
        ["Embedded security addendum attached."],
        extra_trailer_entries=malicious_embed,
    )
