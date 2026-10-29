"""Build and validate the HACS CurbCharge release archive."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "curbcharge"
OUTPUT = ROOT / "dist" / "curbcharge.zip"
VERSION_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
ALLOWED_SUFFIXES = {".js", ".json", ".png", ".py", ".yaml"}
EXCLUDED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    "__pycache__",
    "fixtures",
    "node_modules",
    "tests",
}
SENSITIVE_NAME_PARTS = ("secret", "credential", "token", "password")
REQUIRED_FILES = {
    "__init__.py",
    "afdc.py",
    "api.py",
    "belgium.py",
    "binary_sensor.py",
    "brand/icon.png",
    "brand/icon@2x.png",
    "config_flow.py",
    "const.py",
    "coordinator.py",
    "diagnostics.py",
    "entity.py",
    "geography.py",
    "icons.json",
    "location.py",
    "manifest.json",
    "models.py",
    "nobil.py",
    "provider.py",
    "schema.py",
    "sensor.py",
    "services.py",
    "services.yaml",
    "statistics.py",
    "strings.json",
    "www/curbcharge-card.js",
    *(f"translations/{locale}.json" for locale in ("de", "en", "fr", "nb", "nl", "sv")),
}


def _source_files(integration: Path) -> dict[str, Path]:
    if not integration.is_dir():
        raise ValueError(f"Integration directory does not exist: {integration}")
    files: dict[str, Path] = {}
    for path in sorted(integration.rglob("*")):
        relative = path.relative_to(integration)
        if path.is_symlink():
            raise ValueError(f"Integration contains a symlink: {relative}")
        if not path.is_file():
            continue
        if any(part.lower() in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if relative.name in {".DS_Store", "quality_scale.yaml"}:
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"Unexpected integration file: {relative}")
        if any(part in relative.name.lower() for part in SENSITIVE_NAME_PARTS):
            raise ValueError(
                f"Development or secret file is not packageable: {relative}"
            )
        files[relative.as_posix()] = path
    missing = REQUIRED_FILES - files.keys()
    if missing:
        raise ValueError(
            f"Missing required integration files: {', '.join(sorted(missing))}"
        )
    return files


def _manifest_version(contents: dict[str, bytes]) -> str:
    manifest = json.loads(contents["manifest.json"])
    if not isinstance(manifest, dict) or manifest.get("domain") != "curbcharge":
        raise ValueError("Manifest domain must be 'curbcharge'")
    for key in ("dependencies", "requirements"):
        if not isinstance(manifest.get(key), list) or not all(
            isinstance(value, str) for value in manifest[key]
        ):
            raise ValueError(f"Manifest {key} must be a list of strings")
    version = manifest.get("version")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise ValueError("Manifest version must be stable MAJOR.MINOR.PATCH")
    return version


def validate_archive(
    archive_path: Path, integration: Path = INTEGRATION, release_tag: str | None = None
) -> dict[str, object]:
    """Validate a release ZIP without extracting its contents."""
    expected = set(_source_files(integration))
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if not names or len(names) != len(set(names)):
            raise ValueError("Archive is empty or contains duplicate paths")

        contents: dict[str, bytes] = {}
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if (
                "\\" in name
                or ":" in name
                or path.is_absolute()
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or member.is_dir()
            ):
                raise ValueError(f"Unsafe archive path: {name}")
            relative = path.as_posix()
            if any(part.lower() in EXCLUDED_DIRECTORIES for part in path.parts):
                raise ValueError(f"Development file in archive: {name}")
            if any(part in path.name.lower() for part in SENSITIVE_NAME_PARTS):
                raise ValueError(f"Secret-like file in archive: {name}")
            if PurePosixPath(relative).suffix.lower() not in ALLOWED_SUFFIXES:
                raise ValueError(f"Unexpected archive file: {name}")
            if member.flag_bits & 1:
                raise ValueError(f"Encrypted archive member: {name}")
            mode = member.external_attr >> 16
            if member.create_system == 3 and stat.S_ISLNK(mode):
                raise ValueError(f"Symlink in archive: {name}")
            if member.create_system == 3 and stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise ValueError(f"Non-regular archive member: {name}")
            contents[relative] = archive.read(member)

    if set(contents) != expected:
        missing = sorted(expected - contents.keys())
        extra = sorted(contents.keys() - expected)
        raise ValueError(
            f"Archive file inventory mismatch; missing={missing}, extra={extra}"
        )

    version = _manifest_version(contents)
    if release_tag is not None and release_tag != f"v{version}":
        raise ValueError(f"Release tag {release_tag} does not match manifest {version}")
    for name, data in contents.items():
        if name.endswith(".json"):
            json.loads(data)
        if name.endswith(".py"):
            compile(data.decode("utf-8"), name, "exec")
    translations = {
        name.removeprefix("translations/")
        for name in contents
        if name.startswith("translations/")
    }
    if translations != {
        f"{locale}.json" for locale in ("de", "en", "fr", "nb", "nl", "sv")
    }:
        raise ValueError("Archive is missing a required translation")
    if not contents["www/curbcharge-card.js"].strip():
        raise ValueError("Bundled Lovelace card is empty")
    if json.loads(contents["strings.json"]) != json.loads(
        contents["translations/en.json"]
    ):
        raise ValueError("English translation must match strings.json")

    with zipfile.ZipFile(archive_path) as archive:
        compressed_size = sum(member.compress_size for member in archive.infolist())
        uncompressed_size = sum(member.file_size for member in archive.infolist())
    return {
        "version": version,
        "files": sorted(contents),
        "compressed_size": compressed_size,
        "uncompressed_size": uncompressed_size,
    }


def build_archive(
    output: Path = OUTPUT,
    integration: Path = INTEGRATION,
    release_tag: str | None = None,
) -> dict[str, object]:
    """Create a stable, uncompressed archive matching HACS's ZIP layout."""
    files = _source_files(integration)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative, path in files.items():
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes())
    return validate_archive(output, integration, release_tag)


def _print_inventory(path: Path, inventory: dict[str, object]) -> None:
    files = inventory["files"]
    print(f"Release artifact: {path}")
    print(f"Version: {inventory['version']}")
    print(f"Files: {len(files)}")
    print(f"Compressed size: {inventory['compressed_size']} bytes")
    print(f"Uncompressed size: {inventory['uncompressed_size']} bytes")
    for name in files:
        print(f" - {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build and validate the release ZIP")
    build.add_argument("--output", type=Path, default=OUTPUT)
    build.add_argument("--tag")
    validate = commands.add_parser("validate", help="validate an existing release ZIP")
    validate.add_argument("archive", type=Path)
    validate.add_argument("--tag")
    args = parser.parse_args()

    try:
        if args.command == "build":
            inventory = build_archive(args.output, release_tag=args.tag)
            _print_inventory(args.output, inventory)
        else:
            inventory = validate_archive(args.archive, release_tag=args.tag)
            _print_inventory(args.archive, inventory)
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
        SyntaxError,
    ) as error:
        print(f"Release package validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
