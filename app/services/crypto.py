import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

def encrypt_nse_password(password: str, secret_key_b64: str) -> str:
    """
    Encrypt password using the algorithm described in NSE Extranet FAQ:
    AES-256-ECB with PKCS7 padding; secret key is Base64-decoded first.

    Confirm the exact key encoding against the member registration help
    document before using with live credentials.
    """
    key = base64.urlsafe_b64decode(secret_key_b64)
    if len(key) != 32:
        raise ValueError("NSE AES-256 secret key must decode to 32 bytes")

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(password.encode("utf-8")) + padder.finalize()

    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")
