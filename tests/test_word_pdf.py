import shutil
import subprocess
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from cotizaciones.word_pdf import PdfConversionError, convert_docx_to_pdf


class WordPdfTests(unittest.TestCase):
    def test_uses_libreoffice_when_powershell_is_not_available(self):
        def executable(name):
            return "/usr/bin/libreoffice" if name == "libreoffice" else None

        def conversion(command, **kwargs):
            output_directory = Path(command[command.index("--outdir") + 1])
            (output_directory / "oferta.pdf").write_bytes(b"%PDF-1.7\n%%EOF")
            return subprocess.CompletedProcess(command, 0, "converted", "")

        with (
            patch.object(shutil, "which", side_effect=executable),
            patch(
                "cotizaciones.word_pdf.subprocess.run", side_effect=conversion
            ) as run,
        ):
            result = convert_docx_to_pdf(BytesIO(b"PK corporate docx"))

        self.assertTrue(result.getvalue().startswith(b"%PDF"))
        command = run.call_args.args[0]
        self.assertIn("--headless", command)
        self.assertIn("pdf:writer_pdf_Export", command)

    def test_rejects_generic_pdf_when_no_corporate_converter_exists(self):
        with (
            patch.object(shutil, "which", return_value=None),
            self.assertRaisesRegex(PdfConversionError, "requiere LibreOffice"),
        ):
            convert_docx_to_pdf(BytesIO(b"PK corporate docx"))


if __name__ == "__main__":
    unittest.main()
