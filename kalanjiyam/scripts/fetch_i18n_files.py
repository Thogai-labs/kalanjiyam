import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
TRANSLATIONS_DIR = PROJECT_DIR / "kalanjiyam" / "translations"
POT_FILE = PROJECT_DIR / "messages.pot"
CFG_FILE = PROJECT_DIR / "babel.cfg"
LOCALES = ["ta", "hi_IN", "sa", "te_IN", "en"]


def extract_messages() -> bool:
    """Extract translatable strings into messages.pot."""
    print("Extracting translatable strings to messages.pot...")
    cmd = [
        "pybabel",
        "extract",
        "--mapping",
        str(CFG_FILE),
        "--keywords",
        "_l",
        "--keywords",
        "pgettext:1c,2",
        "--keywords",
        "npgettext:1c,2,3",
        "--output-file",
        str(POT_FILE),
        str(PROJECT_DIR),
    ]
    res = subprocess.run(cmd, check=False)
    return res.returncode == 0


def init_or_update_catalogs() -> bool:
    """Initialize missing locales and update existing catalogs."""
    TRANSLATIONS_DIR.mkdir(parents=True, exist_ok=True)
    for loc in LOCALES:
        po_path = TRANSLATIONS_DIR / loc / "LC_MESSAGES" / "messages.po"
        if not po_path.exists():
            print(f"Initializing catalog for locale '{loc}'...")
            subprocess.run(
                ["pybabel", "init", "-i", str(POT_FILE), "-d", str(TRANSLATIONS_DIR), "-l", loc],
                check=False,
            )

    print("Updating all translation catalogs...")
    res = subprocess.run(
        ["pybabel", "update", "-i", str(POT_FILE), "-d", str(TRANSLATIONS_DIR)],
        check=False,
    )
    return res.returncode == 0


def compile_translations(path: Path = TRANSLATIONS_DIR) -> bool:
    """Compile all .po catalogs to .mo files."""
    print("Compiling translation catalogs to .mo...")
    result = subprocess.run(
        ["pybabel", "compile", "-d", str(path), "-f"],
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    extract_messages()
    init_or_update_catalogs()
    compile_translations(TRANSLATIONS_DIR)
    print("✔ i18n catalogs ready and compiled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
