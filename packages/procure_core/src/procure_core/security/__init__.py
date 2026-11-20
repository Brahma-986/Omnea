"""Security utilities: password hashing, tokens, lockout, and TOTP."""

from procure_core.security.embeddings import TprmEmbeddingService
from procure_core.security.isolated_extractor import (
    ExtractionIsolationError,
    IsolatedPdfExtractor,
)
from procure_core.security.lockout import check_lockout, record_login_attempt
from procure_core.security.malware_scanner import (
    ClamAVScanner,
    HeuristicMalwareScanner,
    IMalwareScanner,
    MalwareScanResult,
    QuarantineManager,
)
from procure_core.security.passwords import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from procure_core.security.pdf_scanner import PdfScanResult, StructuralPdfScanner
from procure_core.security.secrets import (
    EncryptedFileSecretsManager,
    SecretsManagerProtocol,
    get_secrets_manager,
    redact_sensitive_data,
)
from procure_core.security.tokens import (
    create_access_token,
    create_csrf_token,
    create_random_token,
    create_refresh_token,
    decode_access_token,
    hash_token,
)
from procure_core.security.totp import (
    generate_backup_codes,
    generate_totp_secret,
    get_totp_uri,
    verify_totp_code,
)

__all__ = [
    "hash_password",
    "verify_password",
    "validate_password_strength",
    "create_access_token",
    "decode_access_token",
    "create_refresh_token",
    "create_random_token",
    "create_csrf_token",
    "hash_token",
    "check_lockout",
    "record_login_attempt",
    "generate_totp_secret",
    "get_totp_uri",
    "verify_totp_code",
    "generate_backup_codes",
    "StructuralPdfScanner",
    "PdfScanResult",
    "IMalwareScanner",
    "ClamAVScanner",
    "HeuristicMalwareScanner",
    "MalwareScanResult",
    "QuarantineManager",
    "IsolatedPdfExtractor",
    "ExtractionIsolationError",
    "TprmEmbeddingService",
    "SecretsManagerProtocol",
    "EncryptedFileSecretsManager",
    "get_secrets_manager",
    "redact_sensitive_data",
]
