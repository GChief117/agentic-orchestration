"""Read-only, explicit UTF-8 source access inside an imported project's root.

Discovery reads metadata only. Files are limited to 64 KiB each; callers may read
at most eight files and 48,000 decoded characters in one request. Symlinks,
hidden path components, common secret filenames, and unapproved suffixes are
never source attachments. Directory-descriptor walks enforce these boundaries
again when a file is opened, rather than trusting an earlier scan result.
"""

import hashlib
import os
from pathlib import Path
import stat

MAX_FILE_BYTES = 64 * 1024
MAX_SOURCE_FILES = 8
MAX_TOTAL_CHARACTERS = 48000
MAX_SCAN_ENTRIES = 3000
MAX_SCAN_DEPTH = 8
TEXT_SUFFIXES = frozenset(
    (
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".md",
        ".txt",
        ".css",
        ".html",
        ".toml",
        ".yaml",
        ".yml",
        ".go",
        ".rs",
        ".java",
        ".sh",
        ".sql",
        ".csv",
        ".xml",
    )
)
TEXT_NAMES = frozenset(("readme", "license", "dockerfile", "makefile"))
SKIP_DIRECTORIES = frozenset(
    (
        "node_modules",
        "vendor",
        "venv",
        "env",
        "build",
        "dist",
        "target",
        "coverage",
        "library",
        "applications",
        "__pycache__",
    )
)


def _source_root(root):
    """Require an existing absolute canonical directory, rejecting redirected roots."""
    if not isinstance(root, str) or not root.strip():
        raise ValueError("A source folder is required")
    path = Path(root).expanduser()
    try:
        if (
            not path.is_absolute()
            or path.resolve(strict=True) != path
            or not path.is_dir()
        ):
            raise ValueError("Source root must be a canonical existing directory")
    except (OSError, RuntimeError) as exc:
        raise ValueError("Source folder is unavailable") from exc
    return path


def _relative_parts(relative):
    """Validate a canonical relative path and exclude hidden and secret components."""
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or "\x00" in relative
    ):
        raise ValueError("Choose a relative source file path")
    parts = relative.split("/")
    if any(not part or part in (".", "..") for part in parts):
        raise ValueError("Source paths cannot be absolute or contain traversal")
    for part in parts:
        lowered = part.lower()
        if (
            part.startswith(".")
            or lowered.startswith(".env")
            or lowered.endswith((".pem", ".key"))
            or lowered in ("id_rsa", "id_ed25519", "credentials.json")
        ):
            raise ValueError("Hidden files and secret files cannot be attached")
    name = parts[-1].lower()
    if Path(name).suffix not in TEXT_SUFFIXES and name not in TEXT_NAMES:
        raise ValueError("Choose a supported text source file")
    return parts


def _directory_fd(path):
    """Open every absolute directory component without following a symbolic link."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _relative_directory_fd(root_fd, parts):
    """Walk relative directories from an already-open root without following links."""
    descriptor = os.dup(root_fd)
    try:
        for part in parts:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_file(root, parts):
    """Open a regular bounded file securely; the caller owns the returned descriptor."""
    root_fd = _directory_fd(root)
    try:
        parent_fd = _relative_directory_fd(root_fd, parts[:-1])
        try:
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd
            )
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
        os.close(descriptor)
        raise ValueError("Source files must be regular files of at most 64 KiB")
    return descriptor


def source_file(root: str, relative: str) -> Path:
    """Validate one allowed, non-symlink source file of at most 64 KiB.

    Returns its absolute path without reading contents. A returned path is not a
    lasting access grant: use read_source_files for a secure read after selection.
    """
    base = _source_root(root)
    parts = _relative_parts(relative)
    try:
        descriptor = _open_file(base, parts)
    except OSError as exc:
        raise ValueError(
            "Source file is unavailable or crosses a symbolic link"
        ) from exc
    os.close(descriptor)
    return base.joinpath(*parts)


def list_source_files(root) -> dict:
    """List safe text filenames and sizes without opening their contents.

    Traverse at most eight directory levels and inspect at most 3,000 entries.
    Hidden, dependency/build, and symlink directories are skipped. A reached
    limit or unreadable subtree sets truncated so coverage is never overstated.
    """
    base = _source_root(root)
    try:
        root_fd = _directory_fd(base)
    except OSError as exc:
        raise ValueError(
            "Source folder is unavailable or crosses a symbolic link"
        ) from exc
    pending, files, scanned = [()], [], 0
    truncated = False
    try:
        while pending:
            if scanned >= MAX_SCAN_ENTRIES:
                truncated = True
                break
            parts = pending.pop()
            try:
                folder_fd = _relative_directory_fd(root_fd, parts)
                try:
                    with os.scandir(folder_fd) as iterator:
                        entries = []
                        for entry in iterator:
                            if scanned >= MAX_SCAN_ENTRIES:
                                truncated = True
                                break
                            scanned += 1
                            entries.append(entry)
                        for entry in sorted(entries, key=lambda item: item.name):
                            if entry.name.startswith(".") or entry.is_symlink():
                                continue
                            child_parts = (*parts, entry.name)
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name.lower() in SKIP_DIRECTORIES:
                                    continue
                                if len(child_parts) > MAX_SCAN_DEPTH:
                                    truncated = True
                                else:
                                    pending.append(child_parts)
                                continue
                            relative = "/".join(child_parts)
                            try:
                                _relative_parts(relative)
                                metadata = entry.stat(follow_symlinks=False)
                            except (ValueError, OSError):
                                continue
                            if (
                                stat.S_ISREG(metadata.st_mode)
                                and metadata.st_size <= MAX_FILE_BYTES
                            ):
                                files.append(
                                    {"path": relative, "size": metadata.st_size}
                                )
                finally:
                    os.close(folder_fd)
            except OSError:
                truncated = True
    finally:
        os.close(root_fd)
    return {
        "files": sorted(files, key=lambda item: item["path"]),
        "truncated": truncated,
    }


def read_source_files(root, paths: list[str]) -> list[dict]:
    """Read at most eight explicitly selected UTF-8 files, totaling 48,000 characters.

    All path, type, link, and 64 KiB limits are checked again at read time. Reject
    duplicate selections (including aliases to the same inode), invalid UTF-8,
    and NUL-containing binary content. Results include a digest of the read bytes.
    """
    if (
        not isinstance(paths, list)
        or not 1 <= len(paths) <= MAX_SOURCE_FILES
        or any(not isinstance(path, str) for path in paths)
        or len(set(paths)) != len(paths)
    ):
        raise ValueError("Select between one and eight distinct source files")
    base = _source_root(root)
    results, inodes, total = [], set(), 0
    for relative in paths:
        parts = _relative_parts(relative)
        # source_file validates the selection; the second open prevents a path
        # replaced by a symlink after validation from redirecting the actual read.
        source_file(str(base), relative)
        try:
            descriptor = _open_file(_source_root(str(base)), parts)
            metadata = os.fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino)
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            raise ValueError(
                "Source file changed or became unavailable before reading"
            ) from exc
        if identity in inodes:
            raise ValueError("Choose each source file only once")
        inodes.add(identity)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Source file grew beyond the 64 KiB limit")
        try:
            content = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Source files must contain valid UTF-8 text") from exc
        if "\x00" in content:
            raise ValueError("Binary source content cannot be attached")
        total += len(content)
        if total > MAX_TOTAL_CHARACTERS:
            raise ValueError("Selected source files exceed 48,000 characters")
        results.append(
            {
                "path": relative,
                "content": content,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return results
