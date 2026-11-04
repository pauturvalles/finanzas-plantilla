"""Genera el par de claves Ed25519 para la API de Revolut X.

    python scripts/generar_clave_revolutx.py C:\\ruta\\fuera\\del\\repo

Crea private.pem (secreta: va al .env y a los secretos de GitHub como REVX_PRIVATE_KEY)
y public.pem (la que registras en Revolut X → Perfil → API keys).
"""
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

destino = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
if (destino / ".git").exists() or (destino / "src" / "finanzas").exists():
    sys.exit("Elige una carpeta FUERA del repositorio: la clave privada no debe acabar en GitHub.")
destino.mkdir(parents=True, exist_ok=True)
clave = Ed25519PrivateKey.generate()
(destino / "private.pem").write_bytes(clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
(destino / "public.pem").write_bytes(clave.public_key().public_bytes(serialization.Encoding.PEM,
                                                                     serialization.PublicFormat.SubjectPublicKeyInfo))
print(f"Claves creadas en {destino.resolve()}:\n  private.pem → secreta (REVX_PRIVATE_KEY)\n  public.pem  → pégala en Revolut X")
print("\nClave pública:\n" + (destino / "public.pem").read_text())
