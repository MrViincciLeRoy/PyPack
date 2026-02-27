# PyPack — Offline Package Downloader

Download Python packages from `requirements.txt` files hosted on GitHub and get them as `.zip` files ready for offline installation.

## How to Use

### Via GitHub Actions (recommended)

1. Fork or copy this repo to your GitHub account
2. Go to **Actions → Download Packages for Offline Use → Run workflow**
3. Fill in the inputs:

| Input | Description |
|-------|-------------|
| `urls` | One or more GitHub URLs to `requirements.txt` files (newline or space separated) |
| `merge` | Combine all packages into one zip instead of one per requirements file |
| `check_missing` | Scan the repo source code and warn about imports not in requirements |

4. Once done, download the zip artifact from the workflow run summary

### Locally

```bash
pip install requests
python pack.py https://github.com/user/repo/blob/main/requirements.txt
```

Multiple files:
```bash
python pack.py https://github.com/a/b/blob/main/req.txt https://github.com/c/d/blob/main/req.txt
```

Merge into one zip:
```bash
python pack.py <url1> <url2> --merge
```

Check for missing packages:
```bash
python pack.py <url> --check-missing
```

### Installing on offline machine

```bash
pip install --no-index --find-links=. -r requirements.txt
```

(Unzip the package zip into the same folder as your requirements.txt first)
