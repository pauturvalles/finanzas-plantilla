"""Muestra qué ficheros ve la cuenta de servicio en la carpeta de entrada de Drive (diagnóstico)."""
import json
import os

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

load_dotenv()
cred = Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
                                             scopes=["https://www.googleapis.com/auth/drive.readonly"])
drive = build("drive", "v3", credentials=cred)
carpeta = os.environ["DRIVE_FOLDER_ENTRADA"]
print("Cuenta de servicio:", cred.service_account_email)
print("Carpeta:", repr(carpeta))
ficheros = drive.files().list(q=f"'{carpeta}' in parents and trashed = false", fields="files(name,mimeType)").execute()["files"]
print(len(ficheros), "ficheros visibles")
for f in ficheros:
    print(" -", f["name"], "|", f["mimeType"])
