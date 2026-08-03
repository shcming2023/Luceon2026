import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/refine_elegantbook_latex.py"


class AuditOnlyCliTests(unittest.TestCase):
    def test_polish_mode_is_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "main.tex").write_text(r"\documentclass{elegantbook}\begin{document}x\end{document}", encoding="utf-8")
            output = root / "out"
            completed = subprocess.run([
                sys.executable, str(SCRIPT), "--project-dir", str(project), "--out-dir", str(output), "--mode", "polish"
            ], text=True, capture_output=True)
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("invalid choice", completed.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
