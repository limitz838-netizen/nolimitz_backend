"""
================================================================================
  NOLIMITZ — CREDENTIAL ENCRYPTION AT REST
================================================================================

Encrypts MT5 master passwords before they hit Postgres, decrypts them
just-in-time inside the Windows workers. A DB leak no longer hands out full
trading control of every client account.

DESIGN (safe, gradual rollout):
  • encrypt_secret(plain)  -> "enc:<fernet token>"  (or plain if no key set)
  • decrypt_secret(stored) -> plaintext. Handles ALL of:
        - "enc:..." rows (decrypts)
        - legacy plaintext rows (returned unchanged)
        - missing key / missing cryptography lib (returned unchanged)
    so the API can start encrypting NEW saves while old rows keep working.

SETUP (both Render AND every Windows worker box):
  1. pip install cryptography
  2. Generate ONE key (run once, anywhere):
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  3. Set it as env var NOLIMITZ_CRED_KEY on the API host and every worker host.
     SAME key everywhere. Never commit it. Rotate = re-save accounts.

If NOLIMITZ_CRED_KEY is unset, everything degrades to plaintext with a loud
warning — the platform keeps working, just without encryption at rest.
================================================================================
"""

import os
import logging

logger = logging.getLogger("security")

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:          # cryptography not installed
    Fernet = None
    InvalidToken = Exception

_PREFIX = "enc:"
_fernet = None
_warned = False


def _load_key():
    """Step 100: read the key when first needed, not only at import. Some VPS programs import this module before
    .env.local is loaded; without this they would start keyless and fail to read encrypted passwords."""
    global _fernet, _warned
    if _fernet is not None:
        return _fernet
    key = (os.environ.get("NOLIMITZ_CRED_KEY") or "").strip()
    if not key:
        try:
            from dotenv import dotenv_values
            key = (dotenv_values(".env.local").get("NOLIMITZ_CRED_KEY") or "").strip()
        except Exception:
            key = ""
    if key and Fernet is not None:
        try:
            _fernet = Fernet(key.encode())
            return _fernet
        except Exception as e:
            if not _warned:
                logger.error("NOLIMITZ_CRED_KEY is set but invalid (%s) — credentials will NOT be encrypted", e)
    elif key and not _warned:
        logger.warning("NOLIMITZ_CRED_KEY is set but 'cryptography' is not installed — run: pip install cryptography")
    elif not _warned:
        logger.warning("NOLIMITZ_CRED_KEY not set — MT5 passwords stored in PLAINTEXT. Set the key on API + "
                       "workers to enable encryption at rest.")
    _warned = True
    return None


_load_key()


def encryption_enabled() -> bool:
    return _load_key() is not None


def encrypt_secret(plain: str) -> str:
    """Encrypt for storage. Idempotent: already-encrypted values pass through
    unchanged, so calling it twice can never double-wrap a password."""
    if not plain:
        return plain
    if plain.startswith(_PREFIX):        # already encrypted
        return plain
    f = _load_key()
    if f is None:                        # no key -> store as-is (legacy mode)
        return plain
    try:
        return _PREFIX + f.encrypt(plain.encode("utf-8")).decode("ascii")
    except Exception as e:
        logger.error("encrypt_secret failed (%s) — storing plaintext", e)
        return plain


def decrypt_secret(stored: str) -> str:
    """Decrypt for use. Legacy plaintext rows pass through unchanged."""
    if not stored:
        return stored
    if not stored.startswith(_PREFIX):   # legacy plaintext row
        return stored
    f = _load_key()
    if f is None:
        logger.error("Encrypted credential found but NOLIMITZ_CRED_KEY is "
                     "missing/invalid on this machine — login will fail. "
                     "Set the same key used by the API.")
        return stored
    try:
        return f.decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("decrypt_secret: token invalid (wrong key?) — "
                     "returning stored value")
        return stored
    except Exception as e:
        logger.error("decrypt_secret failed: %s", e)
        return stored