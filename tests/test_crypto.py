import base64
import pytest
from app.services.crypto import encrypt_nse_password

def test_encrypt_returns_base64_ciphertext():
    key = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
    encrypted = encrypt_nse_password("test-password", key)
    decoded = base64.b64decode(encrypted)
    assert len(decoded) > 0
    assert len(decoded) % 16 == 0

def test_rejects_wrong_key_length():
    key = base64.urlsafe_b64encode(b"short").decode()
    with pytest.raises(ValueError):
        encrypt_nse_password("password", key)
