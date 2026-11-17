#!/usr/bin/env python3
"""Verify the Longtouzi release signatures and package SHA-256 manifest."""
from __future__ import annotations
import argparse
import base64
import hashlib
import hmac
import re
import sys
from pathlib import Path, PurePosixPath

SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

def read_tlv(data: bytes, offset: int = 0):
    if offset + 2 > len(data):
        raise ValueError("truncated DER data")
    tag = data[offset]
    first = data[offset + 1]
    offset += 2
    if first < 0x80:
        length = first
    else:
        count = first & 0x7f
        if count == 0 or count > 4 or offset + count > len(data):
            raise ValueError("invalid DER length")
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    end = offset + length
    if end > len(data):
        raise ValueError("truncated DER value")
    return tag, data[offset:end], end

def load_public_key(path: Path):
    pem = path.read_text(encoding="ascii")
    match = re.search(r"-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----", pem, re.S)
    if not match:
        raise ValueError("expected a SubjectPublicKeyInfo PEM public key")
    der = base64.b64decode("".join(match.group(1).split()), validate=True)
    tag, outer, end = read_tlv(der)
    if tag != 0x30 or end != len(der):
        raise ValueError("invalid public key DER")
    tag, _algorithm, pos = read_tlv(outer)
    if tag != 0x30:
        raise ValueError("invalid public key algorithm section")
    tag, bit_string, pos = read_tlv(outer, pos)
    if tag != 0x03 or pos != len(outer) or not bit_string or bit_string[0] != 0:
        raise ValueError("invalid public key bit string")
    tag, rsa_sequence, end = read_tlv(bit_string[1:])
    if tag != 0x30:
        raise ValueError("invalid RSA key sequence")
    tag, modulus, pos = read_tlv(rsa_sequence)
    if tag != 0x02:
        raise ValueError("missing RSA modulus")
    tag, exponent, pos = read_tlv(rsa_sequence, pos)
    if tag != 0x02 or pos != len(rsa_sequence):
        raise ValueError("missing RSA exponent")
    return int.from_bytes(modulus, "big"), int.from_bytes(exponent, "big"), der

def verify_rsa_sha256(path: Path, signature_path: Path, modulus: int, exponent: int):
    signature = signature_path.read_bytes()
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        raise ValueError(f"signature length mismatch: {signature_path.name}")
    encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
    digest_info = SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(path.read_bytes()).digest()
    padding_size = size - len(digest_info) - 3
    if padding_size < 8:
        raise ValueError("RSA key is too small")
    expected = b"\x00\x01" + b"\xff" * padding_size + b"\x00" + digest_info
    if not hmac.compare_digest(encoded, expected):
        raise ValueError(f"signature verification failed: {path.name}")

def verify_manifest(root: Path, manifest: Path):
    entries = {}
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        if not match:
            raise ValueError(f"manifest format error on line {line_number}")
        digest, relative = match.groups()
        rel = PurePosixPath(relative)
        if rel.is_absolute() or ".." in rel.parts or relative in entries:
            raise ValueError(f"unsafe or duplicate manifest path: {relative}")
        target = root.joinpath(*rel.parts)
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"missing or linked file: {relative}")
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"manifest path escapes package: {relative}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if not hmac.compare_digest(actual.lower(), digest.lower()):
            raise ValueError(f"SHA-256 mismatch: {relative}")
        entries[relative] = digest.lower()
    omitted = {manifest.relative_to(root).as_posix(), "SHA256SUMS.txt.sig"}
    actual_files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} - omitted
    if actual_files != set(entries):
        extra = sorted(actual_files - set(entries))
        missing = sorted(set(entries) - actual_files)
        raise ValueError(f"manifest coverage mismatch; unlisted={extra[:5]}, missing={missing[:5]}")
    return len(entries)

def main():
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--expected-fingerprint", help="trusted SHA-256 hex fingerprint from a separate trusted source")
    args = parser.parse_args()
    root = args.root.resolve()
    public_key = root / "龙头子_发布公钥.pem"
    manifest = root / "SHA256SUMS.txt"
    manifest_signature = root / "SHA256SUMS.txt.sig"
    workbench = root / "构图分析工作台.html"
    workbench_signature = root / "构图分析工作台.html.sig"
    try:
        modulus, exponent, der = load_public_key(public_key)
        fingerprint = hashlib.sha256(der).hexdigest()
        print("公钥 SHA-256 指纹：" + fingerprint)
        if args.expected_fingerprint:
            expected_fp = args.expected_fingerprint.lower().replace(":", "").strip()
            if not hmac.compare_digest(fingerprint, expected_fp):
                raise ValueError("public key fingerprint differs from the separately trusted fingerprint")
        verify_rsa_sha256(manifest, manifest_signature, modulus, exponent)
        print("发布清单签名：通过")
        verify_rsa_sha256(workbench, workbench_signature, modulus, exponent)
        print("工作台文件签名：通过")
        count = verify_manifest(root, manifest)
        print(f"清单文件哈希：{count} 项全部通过")
        print("验签完成。该结果验证文件完整性，不阻止复制、修改或删除。")
        return 0
    except (OSError, ValueError) as exc:
        print("验签失败：" + str(exc), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
