"""
security.py - Encryption, Hashing & Security Utilities
Analogic Memory System for Omnira Synora AI
AES-256-GCM encryption for all sensitive data
"""

import os
import hashlib
import hmac
import secrets
import base64
import logging
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

# Master encryption key (must be 32 bytes for AES-256)
_MASTER_KEY_HEX = os.getenv("MASTER_ENCRYPTION_KEY", "")
_API_SECRET = os.getenv("API_SECRET_KEY", secrets.token_hex(32))

NONCE_SIZE = 12  # 96-bit nonce for AES-GCM


def _get_master_key() -> bytes:
    """Derive or load master encryption key."""
    if _MASTER_KEY_HEX and len(_MASTER_KEY_HEX) == 64:
        return bytes.fromhex(_MASTER_KEY_HEX)
    
    # Derive from environment secret (for dev/staging)
    secret = os.getenv("SECRET_PASSPHRASE", "analogic-memory-default-passphrase").encode()
    salt = os.getenv("KEY_SALT", "analogic-salt-2024").encode()
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
        backend=default_backend()
    )
    key = kdf.derive(secret)
    logger.warning("Using derived key. Set MASTER_ENCRYPTION_KEY in production.")
    return key


MASTER_KEY = _get_master_key()


def encrypt(plaintext: str) -> bytes:
    """
    Encrypt plaintext using AES-256-GCM.
    Returns: nonce (12 bytes) + ciphertext+tag
    """
    nonce = secrets.token_bytes(NONCE_SIZE)
    aesgcm = AESGCM(MASTER_KEY)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt(encrypted_data: bytes) -> str:
    """
    Decrypt AES-256-GCM encrypted data.
    Input: nonce (12 bytes) + ciphertext+tag
    """
    nonce = encrypted_data[:NONCE_SIZE]
    ciphertext = encrypted_data[NONCE_SIZE:]
    aesgcm = AESGCM(MASTER_KEY)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def hash_content(content: str) -> str:
    """SHA-256 hash for deduplication / integrity checks."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def checksum_bytes(data: bytes) -> str:
    """SHA-256 checksum of raw bytes (used for backups)."""
    return hashlib.sha256(data).hexdigest()


def generate_api_token() -> str:
    """Generate a secure random API token."""
    return secrets.token_urlsafe(48)


def verify_api_token(token: str, stored_hash: str) -> bool:
    """Constant-time API token comparison."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(token_hash, stored_hash)


def hash_api_token(token: str) -> str:
    """Hash an API token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def derive_user_key(user_id: str) -> bytes:
    """
    Derive a per-user sub-key from the master key.
    Enables per-user key rotation without re-encrypting all data.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=user_id.encode(),
        iterations=100_000,
        backend=default_backend()
    )
    return kdf.derive(MASTER_KEY)


def encrypt_with_user_key(plaintext: str, user_id: str) -> bytes:
    """Encrypt using a user-specific derived key."""
    user_key = derive_user_key(user_id)
    nonce = secrets.token_bytes(NONCE_SIZE)
    aesgcm = AESGCM(user_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt_with_user_key(encrypted_data: bytes, user_id: str) -> str:
    """Decrypt using a user-specific derived key."""
    user_key = derive_user_key(user_id)
    nonce = encrypted_data[:NONCE_SIZE]
    ciphertext = encrypted_data[NONCE_SIZE:]
    aesgcm = AESGCM(user_key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def sanitize_input(text: str, max_length: int = 50_000) -> str:
    """Basic input sanitization."""
    if not isinstance(text, str):
        raise ValueError("Input must be a string.")
    text = text.strip()
    if len(text) > max_length:
        raise ValueError(f"Input exceeds maximum length of {max_length} characters.")
    return text
