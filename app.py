import os
import re
import ast
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, render_template, request, send_file, session

app = Flask(__name__)
app.secret_key = os.urandom(24)

STDLIB = {
    "os", "sys", "re", "io", "abc", "ast", "csv", "copy", "math", "json",
    "time", "uuid", "enum", "glob", "gzip", "hmac", "html", "http", "hashlib",
    "heapq", "functools", "datetime", "decimal", "logging", "pathlib", "pickle",
    "platform", "random", "shutil", "signal", "socket", "sqlite3", "string",
    "struct", "subprocess", "tarfile", "tempfile", "threading", "traceback",
    "typing", "unittest", "urllib", "warnings", "weakref", "zipfile", "arg