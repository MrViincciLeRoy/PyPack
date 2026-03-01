import ast
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

STDLIB = {
    "os", "sys", "re", "io", "abc", "ast", "csv", "copy", "math", "json",
    "time", "uuid", "enum", "glob", "gzip", "hmac", "html", "http", "hashlib",
    "heapq", "functools", "datetime", "decimal", "logging", "pathlib", "pickle",
    "platform", "random", "shutil", "signal", "socket", "sqlite3", "string",
    "struct", "subprocess", "tarfile", "tempfile", "threading", "traceback",
    "typing", "unittest", "urllib", "warnings", "weakref", "zipfile", "argparse",
    "collections", "contextlib", "dataclasses", "difflib", "email", "fileinput",
    "fnmatch", "fractions", "getpass", "getopt", "grp", "inspect", "itertools",
    "keyword", "linecache", "locale", "mimetypes", "multiprocessing", "numbers",
    "operator", "optparse", "pdb", "pprint", "queue", "readline", "resource",
    "rlcompleter", "runpy", "sched", "secrets", "select", "shelve", "shlex",
    "smtplib", "sndhdr", "stat", "statistics", "tokenize", "token", "trace",
    "types", "venv", "wave", "xml", "xmlrpc", "zipapp", "zlib", "builtins",
    "_thread", "atexit", "base64", "bdb", "binascii", "binhex", "bisect",
    "cgi", "cgitb", "chunk", "cmath", "code", "codecs", "codeop", "compileall",
    "concurrent", "configparser", "cProfile", "crypt", "curses", "dbm", "dis",
    "doctest", "encodings", "errno", "faulthandler", "fcntl", "formatter",
    "ftplib", "gc", "gettext", "graphlib", "imaplib", "imghdr", "imp",
    "importlib", "lib2to3", "lzma", "mailbox", "mailcap", "marshal", "mmap",
    "modulefinder", "netrc", "nis", "nntplib", "plistlib", "poplib", "posix",
    "posixpath", "pty", "pwd", "py_compile", "pyclbr", "pydoc", "quopri",
    "ssl", "stringprep", "sunau", "symtable", "sysconfig", "syslog",
    "tabnanny", "telnetlib", "termios", "test", "textwrap", "tkinter", "tty",
    "turtle", "turtledemo", "uu", "webbrowser", "winreg", "winsound",
    "wsgiref", "xdrlib", "zipimport", "zoneinfo", "ntpath", "genericpath",
    "asyncio", "calendar", "nbformat",
}

IMPORT_TO_PACKAGE = {
    "cv2": "opencv_python", "PIL": "pillow", "sklearn": "scikit_learn",
    "bs4": "beautifulsoup4", "yaml": "pyyaml", "dotenv": "python_dotenv",
    "git": "gitpython", "Crypto": "pycryptodome", "jwt": "pyjwt",
    "attr": "attrs", "dateutil": "python_dateutil", "magic": "python_magic",
    "usb": "pyusb", "serial": "pyserial", "wx": "wxpython",
    "fitz": "pymupdf", "faiss": "faiss_cpu",
    "googleapiclient": "google_api_python_client", "werkzeug": "werkzeug",
}


def raw_github_url(url: str) -> str:
    url = url.strip()
    if "raw.githubusercontent.com" in url:
        return url
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/(.+)", url)
    if match:
        user, repo, path = match.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{path}"
    raise ValueError(f"Cannot convert to raw URL: {url}")


def fetch_requirements(url: str) -> tuple[list[str], list[str]]:
    """Returns (pip_installable, skipped_git_urls)."""
    raw = raw_github_url(url)
    resp = requests.get(raw, timeout=30)
    resp.raise_for_status()
    pkgs, skipped = [], []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if line.startswith("git+") or (line.startswith("http") and "github.com" in line):
            skipped.append(line)
        else:
            pkgs.append(line)
    return pkgs, skipped


def normalize_pkg_name(pkg: str) -> str:
    return re.split(r"[>=<!;\[]", pkg)[0].strip().lower().replace("-", "_")


def dedup_packages(packages: list[str]) -> list[str]:
    """Keep one entry per package name — prefer pinned (==) versions."""
    seen: dict[str, str] = {}
    for pkg in packages:
        name = normalize_pkg_name(pkg)
        if name not in seen:
            seen[name] = pkg
        else:
            if "==" in pkg and "==" not in seen[name]:
                seen[name] = pkg
    return list(seen.values())


def scan_repo_imports(repo_url: str) -> set[str]:
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)", repo_url)
    if not match:
        return set()
    tmpdir = tempfile.mkdtemp()
    try:
        print("  Cloning repo for import scanning...")
        subprocess.run(
            ["git", "clone", "--depth=1", repo_url, tmpdir],
            capture_output=True, check=True
        )
        imports = set()
        for pyfile in Path(tmpdir).rglob("*.py"):
            try:
                tree = ast.parse(pyfile.read_text(errors="ignore"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.add(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.add(node.module.split(".")[0])
            except Exception:
                continue
        return imports
    except Exception as e:
        print(f"  Warning: Could not scan repo — {e}")
        return set()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def check_missing(req_packages: list[str], imports: set[str]) -> list[str]:
    req_normalized = {normalize_pkg_name(p) for p in req_packages}
    missing = []
    for imp in sorted(imports):
        if imp in STDLIB:
            continue
        mapped = IMPORT_TO_PACKAGE.get(imp, imp).lower().replace("-", "_")
        if mapped not in req_normalized and imp.lower().replace("-", "_") not in req_normalized:
            missing.append(imp)
    return missing


def download_packages(packages: list[str], output_dir: Path, platform: str, python_version: str) -> list[str]:
    """Download Windows/Linux/Mac wheels. Returns failed list."""
    output_dir.mkdir(parents=True, exist_ok=True)
    failed = []

    # Platform-specific flags
    platform_flags = [
        "--platform", platform,
        "--python-version", python_version,
        "--implementation", "cp",
        "--only-binary=:all:",
    ]

    for pkg in packages:
        # Try platform-specific first
        result = subprocess.run(
            [sys.executable, "-m", "pip", "download", "--dest", str(output_dir), *platform_flags, pkg],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            # Fallback: try pure python (any platform) wheels
            result = subprocess.run(
                [sys.executable, "-m", "pip", "download", "--dest", str(output_dir),
                 "--platform", "any", "--python-version", python_version,
                 "--implementation", "py", "--only-binary=:all:", pkg],
                capture_output=True, text=True
            )
        if result.returncode != 0:
            # Last resort: no platform restriction (downloads source/sdist)
            result = subprocess.run(
                [sys.executable, "-m", "pip", "download", "--dest", str(output_dir), pkg],
                capture_output=True, text=True
            )
        if result.returncode != 0:
            print(f"  ✗ Failed: {pkg}")
            for line in result.stderr.splitlines():
                if any(k in line.lower() for k in ["error", "×", "hint", "invalid"]):
                    print(f"    {line.strip()}")
            failed.append(pkg)
        else:
            print(f"  ✓ {pkg}")
    return failed


def write_install_scripts(output_dir: Path, packages: list[str], skipped: list[str]):
    """Write requirements.txt + install.bat + install.sh into the output folder."""

    # Write a clean requirements.txt (just package names, no versions for flexibility)
    req_content = "\n".join(packages) + "\n"
    (output_dir / "requirements.txt").write_text(req_content)

    bat = (
        "@echo off\n"
        "echo Installing packages offline...\n"
        "pip install --no-index --find-links=%~dp0 -r %~dp0requirements.txt\n"
        "echo.\n"
        "echo Done!\n"
        + (
            "echo.\n"
            "echo NOTE: The following entries were skipped (git/url based, install manually):\n"
            + "".join(f"echo   - {s}\n" for s in skipped)
            if skipped else ""
        )
        + "pause\n"
    )
    (output_dir / "install.bat").write_text(bat)

    sh = (
        "#!/bin/bash\n"
        'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "echo Installing packages offline...\n"
        'pip install --no-index --find-links="$SCRIPT_DIR" -r "$SCRIPT_DIR/requirements.txt"\n'
        "echo Done!\n"
        + (
            "echo\n"
            "echo NOTE: The following entries were skipped (git/url based, install manually):\n"
            + "".join(f"echo '  - {s}'\n" for s in skipped)
            if skipped else ""
        )
    )
    (output_dir / "install.sh").write_text(sh)


def create_zip(source_dir: Path, zip_path: Path):
    files = list(source_dir.iterdir())
    if not files:
        print(f"  ⚠ Nothing to zip for {zip_path.name} — skipping")
        return
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.name)
    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"  Created: {zip_path.name} ({size_mb:.1f} MB)")


def is_github_url(s: str) -> bool:
    return s.startswith("https://github.com") or s.startswith("http://github.com")


def main():
    parser = argparse.ArgumentParser(description="Download Python packages for offline use")
    parser.add_argument("inputs", nargs="+",
        help="GitHub URLs to requirements.txt files OR plain package names (e.g. flask django numpy)")
    parser.add_argument("--merge", action="store_true", help="Merge all packages into one zip")
    parser.add_argument("--output-dir", default="packages", help="Output directory (default: packages)")
    parser.add_argument("--check-missing", action="store_true", help="Scan repo for unlisted imports")
    parser.add_argument("--platform", default="win_amd64",
        help="Target platform (default: win_amd64). Options: win_amd64, win32, manylinux2014_x86_64, macosx_11_0_arm64")
    parser.add_argument("--python-version", default="311",
        help="Target Python version digits (default: 311). E.g. 310, 312, 313")
    args = parser.parse_args()
    print(f"  Target: {args.platform} / Python {args.python_version}")

    out_base = Path(args.output_dir)
    out_base.mkdir(exist_ok=True)

    all_packages = []
    all_skipped = []
    results = []

    # Separate URLs from direct package names
    urls = [i for i in args.inputs if is_github_url(i)]
    direct_pkgs = [i for i in args.inputs if not is_github_url(i)]

    # Handle direct package names as a virtual "requirements"
    if direct_pkgs:
        label = "direct_packages"
        print(f"\n[→] Direct packages: {', '.join(direct_pkgs)}")
        results.append((label, direct_pkgs, []))
        all_packages.extend(direct_pkgs)

    for url in urls:
        print(f"\n[→] Processing: {url}")
        try:
            pkgs, skipped = fetch_requirements(url)
            print(f"  Found {len(pkgs)} pip-installable package(s)")
            if skipped:
                print(f"  ⚠ Skipped {len(skipped)} git/url entries (not supported by pip download):")
                for s in skipped:
                    print(f"    - {s}")
                all_skipped.extend(skipped)

            if args.check_missing:
                repo_base = re.match(r"(https://github\.com/[^/]+/[^/]+)", url)
                if repo_base:
                    imports = scan_repo_imports(repo_base.group(1))
                    missing = check_missing(pkgs, imports)
                    if missing:
                        print(f"  ⚠ Possibly unlisted imports: {', '.join(missing)}")
                    else:
                        print("  ✓ All detected imports seem covered")

            results.append((url, pkgs, skipped))
            all_packages.extend(pkgs)

        except Exception as e:
            print(f"  ✗ Failed to fetch: {e}")

    all_failed = []

    if args.merge:
        deduped = dedup_packages(all_packages)
        print(f"\n[→] Downloading {len(deduped)} unique package(s) (merged)...")
        tmp = out_base / "_merged_tmp"
        failed = download_packages(deduped, tmp, args.platform, args.python_version)
        all_failed.extend(failed)
        write_install_scripts(tmp, deduped, all_skipped)
        create_zip(tmp, out_base / "packages_merged.zip")
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        for entry, pkgs, skipped in results:
            if is_github_url(entry):
                label = re.sub(r"[^\w]", "_", entry.split("github.com/")[-1])[:60]
            else:
                label = "_".join(pkgs)[:60]
            print(f"\n[→] Downloading for {label}...")
            tmp = out_base / f"_tmp_{label}"
            failed = download_packages(pkgs, tmp, args.platform, args.python_version)
            all_failed.extend(failed)
            write_install_scripts(tmp, pkgs, skipped)
            create_zip(tmp, out_base / f"{label}.zip")
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'='*60}")
    print(f"✓ Done. Output in: {out_base}/")
    if all_failed:
        print(f"\n⚠ {len(all_failed)} package(s) failed to download:")
        for f in all_failed:
            print(f"  - {f}")
    if all_skipped:
        print(f"\n⚠ {len(all_skipped)} git/url entries skipped (install manually from source):")
        for s in all_skipped:
            print(f"  - {s}")


if __name__ == "__main__":
    main()
