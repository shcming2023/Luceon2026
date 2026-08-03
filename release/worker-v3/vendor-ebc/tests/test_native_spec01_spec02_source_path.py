from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.build_native_spec01_spec02 import materialized_source_path


class MaterializedSourcePathTests(unittest.TestCase):
    def test_resolves_unicode_source_pdf_from_contract(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "inputs/source/数学寒假生活G7 A册.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"%PDF-1.4\n")

            resolved = materialized_source_path(
                root,
                {"source_pdf": {"path": "inputs/source/数学寒假生活G7 A册.pdf"}},
            )

            self.assertEqual(resolved, source.resolve())

    def test_rejects_source_path_outside_exact_source_directory(self) -> None:
        for relative in (
            "../outside.pdf",
            "inputs/mineru/source.pdf",
            "inputs/source/not-a-pdf.txt",
            "inputs/source/nested/source.pdf",
        ):
            with self.subTest(relative=relative), TemporaryDirectory() as temporary:
                root = Path(temporary)
                candidate = root / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(b"test")

                with self.assertRaises(ValueError):
                    materialized_source_path(root, {"source_pdf": {"path": relative}})


if __name__ == "__main__":
    unittest.main()
