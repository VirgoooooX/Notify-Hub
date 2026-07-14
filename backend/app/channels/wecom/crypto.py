from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


class WeComCryptoError(ValueError):
    """A callback failed signature, replay, or encryption validation."""


def callback_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    parts = sorted((token, timestamp, nonce, encrypted))
    return hashlib.sha1("".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()


@dataclass(frozen=True, slots=True)
class WeComCrypto:
    token: str
    encoding_aes_key: str
    corp_id: str
    replay_window_seconds: int = 300

    def __post_init__(self) -> None:
        try:
            key = base64.b64decode(f"{self.encoding_aes_key}=")
        except (ValueError, TypeError) as exc:
            raise WeComCryptoError("invalid EncodingAESKey") from exc
        if len(key) != 32:
            raise WeComCryptoError("EncodingAESKey must decode to 32 bytes")

    @property
    def key(self) -> bytes:
        return base64.b64decode(f"{self.encoding_aes_key}=")

    def verify(
        self,
        *,
        signature: str,
        timestamp: str,
        nonce: str,
        encrypted: str,
        now: int | None = None,
    ) -> None:
        try:
            request_time = int(timestamp)
        except ValueError as exc:
            raise WeComCryptoError("invalid callback timestamp") from exc
        current = int(time.time()) if now is None else now
        if abs(current - request_time) > self.replay_window_seconds:
            raise WeComCryptoError("callback timestamp outside replay window")
        expected = callback_signature(self.token, timestamp, nonce, encrypted)
        if not hmac.compare_digest(signature, expected):
            raise WeComCryptoError("invalid callback signature")

    def decrypt(self, encrypted: str) -> str:
        try:
            ciphertext = base64.b64decode(encrypted, validate=True)
            decryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16])).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
            if len(plaintext) < 20:
                raise WeComCryptoError("decrypted callback is too short")
            message_size = struct.unpack(">I", plaintext[16:20])[0]
            end = 20 + message_size
            message = plaintext[20:end]
            receiver = plaintext[end:].decode("utf-8")
            if not hmac.compare_digest(receiver, self.corp_id):
                raise WeComCryptoError("callback receiver does not match CorpID")
            return message.decode("utf-8")
        except WeComCryptoError:
            raise
        except (ValueError, UnicodeDecodeError) as exc:
            raise WeComCryptoError("invalid encrypted callback") from exc
