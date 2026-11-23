"""Self-signed TLS certificate for the LAN.

iOS Safari only exposes the camera (getUserMedia) on a secure origin, so the
page must be served over HTTPS. We mint a small self-signed CA-style cert that
covers the PC's LAN IP. It is regenerated automatically when the IP changes.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# iOS rejects TLS server certs valid for more than 825 days; stay well under.
VALID_DAYS = 365


def ensure_cert(cert_dir: Path, ip: str) -> tuple[Path, Path]:
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "bbs-cam.crt"
    key_path = cert_dir / "bbs-cam.key"
    meta_path = cert_dir / "meta.json"

    if cert_path.exists() and key_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            expires = dt.datetime.fromisoformat(meta["expires"])
            if meta.get("ip") == ip and expires - dt.datetime.now(dt.timezone.utc) > dt.timedelta(days=7):
                return cert_path, key_path
        except (ValueError, KeyError):
            pass  # corrupt meta -> regenerate

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"BBS-Cam {ip}"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "BaconBitsStudio"),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    expires = now + dt.timedelta(days=VALID_DAYS)
    san = x509.SubjectAlternativeName([
        x509.IPAddress(ipaddress.ip_address(ip)),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.DNSName("localhost"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(expires)
        .add_extension(san, critical=False)
        # CA:TRUE lets iOS install it as a profile and trust it, if you want
        # to get rid of the "not private" warning permanently.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    meta_path.write_text(json.dumps({"ip": ip, "expires": expires.isoformat()}))
    return cert_path, key_path
