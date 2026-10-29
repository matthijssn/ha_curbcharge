"""Verify the HACS release ZIP builder and archive validation."""

import json
import os
import stat
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from scripts.release_package import INTEGRATION, build_archive, validate_archive


def _read_archive(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return {member.filename: archive.read(member) for member in archive.infolist()}


def _make_archive(members: dict[str, bytes], symlink: str | None = None) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, contents in members.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            mode = stat.S_IFLNK | 0o777 if name == symlink else stat.S_IFREG | 0o644
            info.external_attr = mode << 16
            archive.writestr(info, contents)
    return output.getvalue()


class ReleasePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.archive_path = self.directory / "curbcharge.zip"
        build_archive(self.archive_path)
        self.archive = self.archive_path.read_bytes()

    def _validate(
        self, data: bytes | None = None, tag: str | None = None
    ) -> dict[str, object]:
        if data is not None:
            self.archive_path.write_bytes(data)
        return validate_archive(
            self.archive_path,
            INTEGRATION,
            tag if tag is not None else os.environ.get("RELEASE_TAG"),
        )

    def test_build_is_deterministic_and_matches_hacs_layout(self) -> None:
        second = self.directory / "second.zip"
        build_archive(second)
        self.assertEqual(self.archive, second.read_bytes())
        inventory = self._validate()
        self.assertEqual(
            inventory["version"],
            json.loads((INTEGRATION / "manifest.json").read_text())["version"],
        )
        self.assertIn("www/curbcharge-card.js", _read_archive(self.archive))
        self.assertNotIn("quality_scale.yaml", inventory["files"])
        self.assertFalse(
            any(
                "__pycache__" in name or name.endswith(".pyc")
                for name in inventory["files"]
            )
        )

    def test_hacs_release_asset_extracts_directly_into_integration_directory(
        self,
    ) -> None:
        target = self.directory / "custom_components" / "curbcharge"
        with zipfile.ZipFile(self.archive_path) as archive:
            archive.extractall(target)
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], "curbcharge")
        self.assertEqual(
            manifest["version"],
            json.loads((INTEGRATION / "manifest.json").read_text())["version"],
        )
        members = _read_archive(self.archive)
        for name in self._validate()["files"]:
            with self.subTest(name=name):
                installed = target / name
                self.assertEqual(
                    installed.read_bytes(), (INTEGRATION / name).read_bytes()
                )
        for name in (
            "__init__.py",
            "config_flow.py",
            "translations/en.json",
            "brand/icon.png",
            "www/curbcharge-card.js",
        ):
            self.assertIn(name, members)
            self.assertTrue((target / name).is_file())
        self.assertFalse((target / "curbcharge-release").exists())
        self.assertFalse((target / "custom_components").exists())

    def test_missing_runtime_module_is_rejected(self) -> None:
        members = _read_archive(self.archive)
        del members["api.py"]
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            self._validate(_make_archive(members))

    def test_invalid_manifest_json_is_rejected(self) -> None:
        members = _read_archive(self.archive)
        members["manifest.json"] = b"{"
        with self.assertRaises(json.JSONDecodeError):
            self._validate(_make_archive(members))

    def test_manifest_without_dependency_lists_is_rejected(self) -> None:
        members = _read_archive(self.archive)
        name = "manifest.json"
        manifest = json.loads(members[name])
        del manifest["requirements"]
        members[name] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, "requirements"):
            self._validate(_make_archive(members))

    def test_release_tag_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            self._validate(tag="v0.0.0")

    def test_prefixed_archive_layouts_are_rejected(self) -> None:
        members = _read_archive(self.archive)
        for prefix in (
            "curbcharge-release/custom_components/curbcharge/",
            "custom_components/curbcharge/",
            "curbcharge/",
            "curbcharge-release/",
        ):
            with self.subTest(prefix=prefix):
                prefixed = {
                    prefix + name: contents for name, contents in members.items()
                }
                with self.assertRaisesRegex(ValueError, "inventory mismatch"):
                    self._validate(_make_archive(prefixed))

    def test_path_traversal_is_rejected(self) -> None:
        for path in (
            "../escape.py",
            "www/../escape.py",
            "/escape.py",
            "C:/escape.py",
        ):
            with self.subTest(path=path):
                members = _read_archive(self.archive)
                members[path] = b""
                with self.assertRaisesRegex(ValueError, "Unsafe archive path"):
                    self._validate(_make_archive(members))

    def test_development_files_are_rejected(self) -> None:
        members = _read_archive(self.archive)
        members["tests/test_extra.py"] = b""
        with self.assertRaisesRegex(ValueError, "Development file"):
            self._validate(_make_archive(members))

    def test_symlinks_are_rejected(self) -> None:
        name = "__init__.py"
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self._validate(_make_archive({name: b"target"}, symlink=name))

    def test_malformed_zip_is_rejected(self) -> None:
        with self.assertRaises(zipfile.BadZipFile):
            self._validate(b"not a ZIP archive")


if __name__ == "__main__":
    unittest.main()
