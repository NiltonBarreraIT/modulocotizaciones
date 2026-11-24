import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path


class PdfConversionError(RuntimeError):
    pass


POWERSHELL_CONVERSION_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$inputPath = $env:ITS_OFFER_DOCX
$outputPath = $env:ITS_OFFER_PDF
$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($inputPath, $false, $true)
    $document.ExportAsFixedFormat($outputPath, 17)
} finally {
    if ($document -ne $null) { $document.Close(0) }
    if ($word -ne $null) { $word.Quit() }
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
}
"""


def _convert_with_word(input_path, output_path, timeout):
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return None

    environment = os.environ.copy()
    environment["ITS_OFFER_DOCX"] = str(input_path.resolve())
    environment["ITS_OFFER_PDF"] = str(output_path.resolve())
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Sta",
            "-Command",
            POWERSHELL_CONVERSION_SCRIPT,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )
    return result


def _convert_with_libreoffice(input_path, output_path, temporary, timeout):
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        return None

    profile = Path(temporary) / "libreoffice-profile"
    profile_uri = profile.resolve().as_uri()
    result = subprocess.run(
        [
            executable,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(Path(temporary).resolve()),
            str(input_path.resolve()),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


def convert_docx_to_pdf(content, timeout=120):
    with tempfile.TemporaryDirectory(prefix="its-offer-") as temporary:
        input_path = Path(temporary) / "oferta.docx"
        output_path = Path(temporary) / "oferta.pdf"
        input_path.write_bytes(content.getvalue())

        try:
            result = _convert_with_word(input_path, output_path, timeout)
            converter = "Microsoft Word"
            if result is None:
                result = _convert_with_libreoffice(
                    input_path, output_path, temporary, timeout
                )
                converter = "LibreOffice"
        except subprocess.TimeoutExpired as error:
            raise PdfConversionError(
                "La conversión del formato corporativo excedió el tiempo permitido"
            ) from error
        if result is None:
            raise PdfConversionError(
                "El servidor requiere LibreOffice para generar el PDF corporativo"
            )
        if result.returncode != 0 or not output_path.is_file():
            detail = (result.stderr or result.stdout or "Error desconocido").strip()
            raise PdfConversionError(
                f"{converter} no pudo generar la oferta PDF: {detail[:500]}"
            )
        pdf = output_path.read_bytes()
        if not pdf.startswith(b"%PDF"):
            raise PdfConversionError(f"{converter} generó un PDF inválido")
        return BytesIO(pdf)
