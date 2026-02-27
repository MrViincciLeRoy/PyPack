import os
import re
import ast
import sys
import shutil
import zipfile
import argparse
import subprocess
import tempfile
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
    "_thread", "abc", "atexit", "base64", "bdb", "binascii", "binhex",
    "bisect", "cgi", "cgitb", "chunk", "cmath", "code", "codecs", "codeop",
    "compileall", "concurrent", "configparser", "cProfile", "crypt", "curses",
    "dbm", "dis", "doctest", "encodings", "errno", "faulthandler", "fcntl",
    "formatter", "ftplib", "gc", "gettext", "graphlib", "imaplib", "imghdr",
    "imp", "importlib", "lib2to3", "lzma", "mailbox", "mailcap", "marshal",
    "mmap", "modulefinder", "netrc", "nis", "nntplib", "plistlib", "poplib",
    "posix", "posixpath", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
    "quopri", "sre_compile", "sre_constants", "sre_parse", "ssl", "stringprep",
    "sunau", "symtable", "sysconfig", "syslog", "tabnanny", "telnetlib",
    "termios", "test", "textwrap", "tkinter", "tty", "turtle", "turtledemo",
    "uu", "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xmlrpc",
    "zipimport", "zoneinfo", "ntpath", "posixpath", "genericpath"
}

def raw_github_url(url: str) -> str:
    """Convert GitHub URL to raw content URL."""
    url = url.strip()
    if "raw.githubusercontent.com" in url:
        return url
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/(.+)", url)
    if match:
        user, repo, path = match.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{path}"
    raise ValueError(f"Cannot convert to raw URL: {url}")

def fetch_requirements(url: str) -> list[str]:
    raw = raw_github_url(url)
    resp = requests.get(raw, timeout=30)
    resp.raise_for_status()
    pkgs = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            pkgs.append(line)
    return pkgs

def scan_repo_imports(repo_url: str) -> set[str]:
    """Clone repo and scan all .py files for imports."""
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)", repo_url)
    if not match:
        return set()
    tmpdir = tempfile.mkdtemp()
    try:
        print(f"  Cloning repo for import scanning...")
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

def normalize_pkg_name(pkg: str) -> str:
    return re.split(r"[>=<!;\[]", pkg)[0].strip().lower().replace("-", "_")

def check_missing(req_packages: list[str], imports: set[str]) -> list[str]:
    req_normalized = {normalize_pkg_name(p) for p in req_packages}
    # Common import→package name mappings
    known_mappings = {
        "cv2": "opencv_python", "PIL": "pillow", "sklearn": "scikit_learn",
        "bs4": "beautifulsoup4", "yaml": "pyyaml", "dotenv": "python_dotenv",
        "git": "gitpython", "Crypto": "pycryptodome", "jwt": "pyjwt",
        "attr": "attrs", "dateutil": "python_dateutil", "magic": "python_magic",
        "usb": "pyusb", "serial": "pyserial", "wx": "wxpython",
    }
    missing = []
    for imp in sorted(imports):
        if imp in STDLIB:
            continue
        mapped = known_mappings.get(imp, imp).lower().replace("-", "_")
        if mapped not in req_normalized and imp.lower().replace("-", "_") not in req_normalized:
            missing.append(imp)
    return missing

def download_packages(packages: list[str], output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading {len(packages)} package(s)...")
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--dest", str(output_dir), *packages],
        check=True
    )

def create_zip(source_dir: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in source_dir.iterdir():
            zf.write(f, f.name)
    print(f"  Created: {zip_path}")


def main():
    parser = argparse.ArgumentParser(description="Download Python packages for offline use")
    parser.add_argument("urls", nargs="+", help="GitHub URLs to requirements.txt files")
    parser.add_argument("--merge", action="store_true", help="Merge all packages into one zip")
    parser.add_argument("--output-dir", default="packages", help="Output directory (default: packages)")
    parser.add_argument("--check-missing", action="store_true", help="Scan repo for unlisted imports")
    args = parser.parse_args()

    out_base = Path(args.output_dir)
    out_base.mkdir(exist_ok=True)

    all_packages = []
    results = []

    for url in args.urls:
        print(f"\n[→] Processing: {url}")
        try:
            pkgs = fetch_requirements(url)
            print(f"  Found {len(pkgs)} package(s) in requirements")

            if args.check_missing:
                repo_base = re.match(r"(https://github\.com/[^/]+/[^/]+)", url)
                if repo_base:
                    imports = scan_repo_imports(repo_base.group(1))
                    missing = check_missing(pkgs, imports)
                    if missing:
                        print(f"  ⚠ Possibly unlisted imports: {', '.join(missing)}")
                    else:
                        print(f"  ✓ All detected imports seem covered")

            results.append((url, pkgs))
            all_packages.extend(pkgs)

        except Exception as e:
            print(f"  ✗ Failed: {e}")

    if args.merge:
        tmp = out_base / "_merged_tmp"
        download_packages(list(set(all_packages)), tmp)
        create_zip(tmp, out_base / "packages_merged.zip")
        shutil.rmtree(tmp)
    else:
        for url, pkgs in results:
            label = re.sub(r"[^\w]", "_", url.split("github.com/")[-1])[:60]
            tmp = out_base / f"_tmp_{label}"
            download_packages(pkgs, tmp)
            create_zip(tmp, out_base / f"{label}.zip")
            shutil.rmtree(tmp)

    print(f"\n✓ Done. Output in: {out_base}/")


if __name__ == "__main__":
    main()
