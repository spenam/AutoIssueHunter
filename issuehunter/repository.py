
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from issuehunter.config import get_settings


class RepositoryError(Exception):
    """Raised when repository operations fail."""


@dataclass
class RepositoryInfo:
    """Basic information collected from a local repository."""

    full_name: str
    path: Path
    files: list[str]
    project_files: list[str]
    primary_language: str | None


class RepositoryInspector:
    """Clones and inspects repositories without executing their code."""

    IGNORED_DIRECTORIES = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "vendor",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
    }

    PROJECT_FILES = {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "Pipfile",
        "poetry.lock",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "Gemfile",
        "composer.json",
        "Makefile",
        "README.md",
    }

    LANGUAGE_EXTENSIONS = {
        ".py": "Python",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".rb": "Ruby",
        ".php": "PHP",
        ".cs": "C#",
        ".cpp": "C++",
        ".c": "C",
    }

    def __init__(self, workspace: Path | None = None) -> None:
        settings = get_settings()
        self.workspace = workspace or settings.workspace_path
        self.workspace.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _run_git(
        arguments: list[str],
        cwd: Path | None = None,
        timeout: int = 120,
    ) -> str:
        """Execute a Git command without invoking a shell."""
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env={
                    "PATH": __import__("os").environ.get("PATH", ""),
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                },
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RepositoryError(f"Git command failed: {exc}") from exc

        if result.returncode != 0:
            raise RepositoryError(
                f"Git command failed: {result.stderr.strip()[:1000]}"
            )

        return result.stdout.strip()

    @staticmethod
    def _repository_parts(full_name: str) -> tuple[str, str]:
        """Validate and split an owner/repository identifier."""
        parts = full_name.split("/")

        if (
            len(parts) != 2
            or not all(parts)
            or any(part in {".", ".."} for part in parts)
            or any(
                not all(char.isalnum() or char in "-_." for char in part)
                for part in parts
            )
        ):
            raise ValueError(
                "Repository must use the format 'owner/repository'."
            )

        return parts[0], parts[1]

    def clone(self, full_name: str) -> Path:
        """
        Clone a public GitHub repository or update an existing clone.

        Does not initialize submodules or execute repository code.
        """
        owner, repository = self._repository_parts(full_name)
        destination = self.workspace / owner / repository

        if destination.exists():
            if not (destination / ".git").is_dir():
                raise RepositoryError(
                    f"Destination exists but is not a Git repository: {destination}"
                )

            self._run_git(
                [
                    "-c", "core.hooksPath=/dev/null",
                    "-c", "protocol.file.allow=never",
                    "fetch", "--depth=1", "origin",
                ],
                cwd=destination,
            )

            self._run_git(
                [
                    "-c", "core.hooksPath=/dev/null",
                    "-c", "protocol.file.allow=never",
                    "reset", "--hard", "FETCH_HEAD",
                ],
                cwd=destination,
            )

        else:
            destination.parent.mkdir(parents=True, exist_ok=True)

            self._run_git(
                [
                    "-c", "core.hooksPath=/dev/null",
                    "-c", "protocol.file.allow=never",
                    "clone",
                    "--depth=1",
                    "--no-recurse-submodules",
                    f"https://github.com/{owner}/{repository}.git",
                    str(destination),
                ],
            )

        return destination

    def inspect(self, full_name: str) -> RepositoryInfo:
        """Inspect a cloned repository's file structure."""
        owner, repository = self._repository_parts(full_name)
        path = self.workspace / owner / repository

        if not (path / ".git").is_dir():
            raise RepositoryError(
                f"Repository has not been cloned: {full_name}"
            )

        files: list[str] = []
        project_files: list[str] = []
        language_counts: dict[str, int] = {}

        for current_path, directories, filenames in __import__(
            "os"
        ).walk(path, followlinks=False):

            directories[:] = [
                directory
                for directory in directories
                if directory not in self.IGNORED_DIRECTORIES
                and not (Path(current_path) / directory).is_symlink()
            ]

            for filename in filenames:
                file_path = Path(current_path) / filename
                relative_path = file_path.relative_to(path).as_posix()

                if file_path.is_symlink():
                    continue

                if not self._is_safe_relative_path(relative_path):
                    continue

                files.append(relative_path)

                if filename in self.PROJECT_FILES:
                    project_files.append(relative_path)

                extension = file_path.suffix.lower()
                language = self.LANGUAGE_EXTENSIONS.get(extension)

                if language:
                    language_counts[language] = (
                        language_counts.get(language, 0) + 1
                    )

        primary_language = (
            max(language_counts, key=language_counts.get)
            if language_counts
            else None
        )

        return RepositoryInfo(
            full_name=full_name,
            path=path,
            files=sorted(files),
            project_files=sorted(project_files),
            primary_language=primary_language,
        )

    @staticmethod
    def _is_safe_relative_path(relative_path: str) -> bool:
        """Reject paths that escape the repository directory."""
        path = PurePosixPath(relative_path)

        return (
            not path.is_absolute()
            and ".." not in path.parts
        )

    def read_file(
        self,
        full_name: str,
        relative_path: str,
        max_bytes: int = 100_000,
    ) -> str:
        """Read a text file from a cloned repository with size limits."""
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive.")

        owner, repository = self._repository_parts(full_name)
        root = (self.workspace / owner / repository).resolve()

        relative = PurePosixPath(relative_path)

        if not self._is_safe_relative_path(relative_path):
            raise ValueError("Unsafe repository file path.")

        target = root.joinpath(*relative.parts)

        if target.is_symlink():
            raise RepositoryError("Refusing to read a symbolic link.")

        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise RepositoryError(
                "File does not exist or escapes the repository."
            ) from exc

        if not resolved.is_file():
            raise RepositoryError("Requested path is not a regular file.")

        if resolved.stat().st_size > max_bytes:
            raise RepositoryError(
                f"File exceeds the {max_bytes}-byte reading limit."
            )

        try:
            return resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RepositoryError(
                "File is not valid UTF-8 text."
            ) from exc
