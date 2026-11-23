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


def convert_docx_to_pdf(content, timeout=120):
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise PdfConversionError(
            "Microsoft Word requiere PowerShell para generar la oferta PDF"
        )

    with tempfile.TemporaryDirectory(prefix="its-offer-") as temporary:
        input_path = Path(temporary) / "oferta.docx"
        output_path = Path(temporary) / "oferta.pdf"
        input_path.write_bytes(content.getvalue())
        environment = os.environ.copy()
        environment["ITS_OFFER_DOCX"] = str(input_path.resolve())
        environment["ITS_OFFER_PDF"] = str(output_path.resolve())
        try:
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
        except subprocess.TimeoutExpired as error:
            raise PdfConversionError(
                "Microsoft Word excedió el tiempo para generar la oferta PDF"
            ) from error
        if result.returncode != 0 or not output_path.is_file():
            detail = (result.stderr or result.stdout or "Error desconocido").strip()
            raise PdfConversionError(
                f"Microsoft Word no pudo generar la oferta PDF: {detail[:500]}"
            )
        pdf = output_path.read_bytes()
        if not pdf.startswith(b"%PDF"):
            raise PdfConversionError("Microsoft Word generó un PDF inválido")
        return BytesIO(pdf)
