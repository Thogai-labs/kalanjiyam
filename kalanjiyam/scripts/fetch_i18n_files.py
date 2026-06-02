import shutil
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/AnaadiAI/kalanjiyam-i18n.git"
PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data" / "kalanjiyam-i18n"


def fetch_git_repo(url: str, path: Path) -> bool:
    """Fetch the latest version of the given repo. Returns False on failure."""
    if path.exists() and not (path / ".git").is_dir():
        if not any(path.iterdir()):
            shutil.rmtree(path)
        else:
            print(
                f"WARNING: {path} exists but is not a git checkout; skipping i18n fetch.",
                file=sys.stderr,
            )
            return False

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--branch=main", url, str(path)],
            check=False,
        )
        if result.returncode != 0:
            print(
                f"WARNING: could not clone {url} (private repo or network). "
                "App will run in English only.",
                file=sys.stderr,
            )
            return False

    for cmd in (
        ["git", "fetch", "origin"],
        ["git", "checkout", "main"],
        ["git", "reset", "--hard", "origin/main"],
    ):
        result = subprocess.run(cmd, cwd=path, check=False)
        if result.returncode != 0:
            print(
                f"WARNING: git update failed in {path}; skipping i18n fetch.",
                file=sys.stderr,
            )
            return False

    return True


def compile_translations(path: Path) -> bool:
    result = subprocess.run(
        ["pybabel", "compile", "-d", str(path)],
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def copy_translation_files(src_dir: Path, dest_dir: Path) -> None:
    shutil.copytree(str(src_dir), str(dest_dir), dirs_exist_ok=True)


def main() -> int:
    src_dir = DATA_DIR / "translations"
    dest_dir = PROJECT_DIR / "kalanjiyam" / "translations"
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not fetch_git_repo(REPO, DATA_DIR):
        return 0

    if not src_dir.is_dir():
        print(
            f"WARNING: {src_dir} not found after clone; skipping i18n install.",
            file=sys.stderr,
        )
        return 0

    compile_translations(src_dir)
    copy_translation_files(src_dir, dest_dir)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
