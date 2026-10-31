"""Local TLS certificate and terminal pairing address."""
import hashlib
import ipaddress
from pathlib import Path
import socket
import subprocess


def lan_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(('192.0.2.1', 9))
            return sock.getsockname()[0]
        except OSError:
            return '127.0.0.1'


def certificate(directory, address):
    # Restrict names before embedding them in OpenSSL's extension arguments.
    ipaddress.ip_address(address)
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = hashlib.sha256(address.encode()).hexdigest()[:16]
    cert, key = folder / f'{stem}.crt', folder / f'{stem}.key'
    if cert.exists() and key.exists():
        key.chmod(0o600)
        return cert, key
    key.touch(mode=0o600, exist_ok=True)
    key.chmod(0o600)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '30',
                    '-subj', '/CN=Bench Coach Phone',
                    '-addext', f'subjectAltName=IP:{address}',
                    '-addext', 'basicConstraints=critical,CA:TRUE'],
                   check=True, capture_output=True)
    return cert, key


def print_phone_url(address, port):
    import qrcode
    url = f'https://{address}:{port}/phone'
    print(f'Phone camera: {url}', flush=True)
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
