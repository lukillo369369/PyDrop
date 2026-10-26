"""Protection checks for release builds that might encounter editable projects."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pydrop_windows_build", ROOT/"tools"/"build_windows.py")
build_windows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_windows)


class DesktopBuildPreservationTests(unittest.TestCase):
    def test_existing_bundle_preserves_editable_project_before_invoking_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/"release"
            bundle = output/"PyDrop"
            bundle.mkdir(parents=True)
            project = bundle/"my-project.drop.json"
            project.write_text('{"editable": true}\n', encoding="utf-8")
            original = project.read_bytes()
            with mock.patch.object(build_windows, "preflight") as preflight, mock.patch.object(build_windows, "_run") as run:
                with self.assertRaisesRegex(build_windows.BuildError, "already exists"):
                    build_windows.build(output, skip_installer=True)
                preflight.assert_not_called()
                run.assert_not_called()
            self.assertEqual(project.read_bytes(), original)

    def test_fresh_output_validation_creates_no_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/"fresh-release"
            self.assertEqual(build_windows._validate_output_directory(output), output.resolve())
            self.assertFalse(output.exists())

    def test_source_checkout_is_rejected(self):
        with self.assertRaisesRegex(build_windows.BuildError, "source checkout"):
            build_windows._validate_output_directory(ROOT)


if __name__ == "__main__":
    unittest.main()

