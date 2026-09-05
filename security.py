import re
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ph = PasswordHasher()
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,4}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")


def hash_password(password):
    return ph.hash(password)


def verify_password(stored, password):
    try:
        return ph.verify(stored, password)
    except VerifyMismatchError:
        return False


def valid_email(value):
    return bool(EMAIL_RE.fullmatch(value or "")) and len(value) <= 254


def valid_package(value):
    return bool(PACKAGE_RE.fullmatch(value or "")) and len(value) <= 128


def valid_version(value):
    return bool(VERSION_RE.fullmatch(value or ""))


def new_id():
    return secrets.token_urlsafe(16)
