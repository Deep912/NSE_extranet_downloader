import base64
import binascii

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding


def encrypt_nse_password(password: str, secret_key_b64: str) -> str:
    """Encrypt an NSE Extranet password using AES-256-ECB and PKCS7 padding."""
    try:
        key = base64.b64decode(secret_key_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("NSE secret key must be valid Base64") from exc

    if len(key) != 32:
        raise ValueError("NSE AES-256 secret key must decode to exactly 32 bytes")
    if not password:
        raise ValueError("NSE password cannot be empty")

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(password.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")
