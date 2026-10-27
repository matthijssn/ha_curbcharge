"""Verify the exact CurbCharge integration subtree in a source archive."""

import json
import os
import re
import subprocess
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

_PREFIX = "custom_components/curbcharge/"
_ROOT = Path(__file__).resolve().parents[1]


def _validate_archive(
    archive_bytes: bytes, expected_files: set[str], release_tag: str | None = None
) -> str:
    """Validate the integration subtree and its manifest in a source archive."""
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        members = {
            member.filename for member in archive.infolist() if not member.is_dir()
        }
        if not members or any(not member.startswith(_PREFIX) for member in members):
            raise ValueError("Archive does not contain only the CurbCharge subtree")

        files = {member.removeprefix(_PREFIX) for member in members}
        if files != expected_files:
            raise ValueError("Archive files do not match the tracked integration tree")

        for path in files:
            if path.endswith(".json"):
                json.loads(archive.read(f"{_PREFIX}{path}"))
        manifest = json.loads(archive.read(f"{_PREFIX}manifest.json"))

    if not isinstance(manifest, dict):
        raise ValueError("Manifest is not a JSON object")
    version = manifest.get("version")
    if (
        not isinstance(version, str)
        or re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
        is None
    ):
        raise ValueError("Manifest version is not stable SemVer")
    if release_tag is not None and release_tag != f"v{version}":
        raise ValueError("Release tag does not match the archived manifest")
    return version


def _make_archive(files: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(f"{_PREFIX}{path}", content)
    return output.getvalue()


class ReleasePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracked_files = {
            path.removeprefix(_PREFIX)
            for path in subprocess.check_output(
                ["git", "ls-files", "--", "custom_components/curbcharge"],
                text=True,
                cwd=_ROOT,
            ).splitlines()
        }
        self.files = {
            path: (_ROOT / _PREFIX / path).read_bytes() for path in self.tracked_files
        }
        self.archive = _make_archive(self.files)

    def test_tracked_archive_matches_manifest_and_release_tag(self) -> None:
        version = _validate_archive(
            self.archive, self.tracked_files, os.environ.get("RELEASE_TAG")
        )
        self.assertRegex(
            version, r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
        )

    def test_missing_runtime_module_is_rejected(self) -> None:
        files = dict(self.files)
        del files["api.py"]
        with self.assertRaisesRegex(ValueError, "tracked integration tree"):
            _validate_archive(_make_archive(files), self.tracked_files)

    def test_invalid_json_is_rejected(self) -> None:
        files = dict(self.files)
        files["manifest.json"] = b"{"
        with self.assertRaises(json.JSONDecodeError):
            _validate_archive(_make_archive(files), self.tracked_files)

    def test_release_tag_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            _validate_archive(self.archive, self.tracked_files, "not-a-release-tag")

    def test_wrong_archive_layout_is_rejected(self) -> None:
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("curbcharge/manifest.json", self.files["manifest.json"])
        with self.assertRaisesRegex(ValueError, "subtree"):
            _validate_archive(output.getvalue(), self.tracked_files)


if __name__ == "__main__":
    unittest.main()
