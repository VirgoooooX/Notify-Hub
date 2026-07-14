import base64
import os
import struct

import pytest
from app.channels.wecom.callback import parse_callback
from app.channels.wecom.crypto import WeComCrypto, WeComCryptoError, callback_signature
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


def _encrypt(key: bytes, corp_id: str, message: str) -> str:
    raw = (
        os.urandom(16)
        + struct.pack(">I", len(message.encode()))
        + message.encode()
        + corp_id.encode()
    )
    padder = PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def test_callback_is_verified_decrypted_and_normalized() -> None:
    key = bytes(range(32))
    encoded_key = base64.b64encode(key).decode().rstrip("=")
    crypto = WeComCrypto("callback-token", encoded_key, "corp-id")
    timestamp = "1784016000"
    inner = (
        "<xml><FromUserName>vigoss</FromUserName><CreateTime>1784016000</CreateTime>"
        "<MsgType>text</MsgType><Content>明天下午三点提醒我交电费</Content>"
        "<MsgId>msg-1</MsgId></xml>"
    )
    encrypted = _encrypt(key, "corp-id", inner)
    signature = callback_signature("callback-token", timestamp, "nonce", encrypted)
    outer = f"<xml><Encrypt>{encrypted}</Encrypt></xml>".encode()

    callback = parse_callback(
        crypto,
        signature=signature,
        timestamp=timestamp,
        nonce="nonce",
        body=outer,
        now=1784016000,
    )

    assert callback.sender_external_id == "vigoss"
    assert callback.provider_message_id == "msg-1"
    assert callback.text == "明天下午三点提醒我交电费"
    assert len(callback.dedupe_key) == 64


def test_callback_replay_and_signature_are_rejected() -> None:
    key = base64.b64encode(bytes(range(32))).decode().rstrip("=")
    crypto = WeComCrypto("token", key, "corp")
    with pytest.raises(WeComCryptoError, match="replay"):
        crypto.verify(
            signature="invalid",
            timestamp="100",
            nonce="n",
            encrypted="e",
            now=1000,
        )
    with pytest.raises(WeComCryptoError, match="signature"):
        crypto.verify(
            signature="invalid",
            timestamp="1000",
            nonce="n",
            encrypted="e",
            now=1000,
        )
