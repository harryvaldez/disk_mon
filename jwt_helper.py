import base64
import hmac
import hashlib
import json
import time

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

def generate_jwt(payload: dict, secret: str, expires_in: int = 300, headers: dict | None = None) -> str:
    iat = int(time.time())
    exp = iat + int(expires_in)
    body = dict(payload or {})
    body.setdefault('iat', iat)
    body.setdefault('exp', exp)
    head = {'alg': 'HS256', 'typ': 'JWT'}
    if headers:
        head.update(headers)
    h_enc = _b64url(json.dumps(head, separators=(',', ':')).encode('utf-8'))
    p_enc = _b64url(json.dumps(body, separators=(',', ':')).encode('utf-8'))
    signing_input = f"{h_enc}.{p_enc}".encode('utf-8')
    sig = hmac.new(secret.encode('utf-8'), signing_input, hashlib.sha256).digest()
    s_enc = _b64url(sig)
    return f"{h_enc}.{p_enc}.{s_enc}"
