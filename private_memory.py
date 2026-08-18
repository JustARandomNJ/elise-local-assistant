from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import json
from pathlib import Path
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


VAULT_VERSION = 1
KDF_ITERATIONS = 600_000
ALLOWED_PRIVATE_POLICIES = {
    "explicit_only",
    "never_prompt",
    "never_model_context",
}
ALLOWED_CATEGORIES = {
    "fact",
    "preference",
    "goal",
    "project",
    "observation",
    "relationship_context",
    "routine",
    "constraint",
    "skill",
    "communication_style",
}


class PrivateMemoryError(RuntimeError):
    pass


class PrivateMemoryLockedError(PrivateMemoryError):
    pass


@dataclass(frozen=True)
class PrivateMemoryRecord:
    id: str
    content: str
    category: str
    retrieval_policy: str
    expires_at: str | None
    created_at: str
    updated_at: str


class PrivateMemoryVault:
    """
    Passphrase-protected encrypted storage for sensitive memories.

    The passphrase is never written to disk. A random salt is stored locally,
    and a Fernet key is derived with PBKDF2 for each unlocked session.
    """

    def __init__(
        self,
        vault_path: str | Path,
        salt_path: str | Path,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.salt_path = Path(salt_path)
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self.salt_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet: Fernet | None = None
        self._data: dict[str, Any] | None = None

    @property
    def is_unlocked(self) -> bool:
        return self._fernet is not None and self._data is not None

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _normalize_expiration(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or cleaned.lower() == "never":
            return None
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
                parsed = datetime.fromisoformat(cleaned + "T23:59:59+00:00")
            else:
                parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError as error:
            raise ValueError("Expiration must be YYYY-MM-DD, an ISO timestamp, or never.") from error
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _is_expired(expires_at: str | None) -> bool:
        if not expires_at:
            return False
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)

    def _derive_fernet(self, passphrase: str, salt: bytes) -> Fernet:
        if len(passphrase) < 10:
            raise ValueError("Private-memory passphrase must contain at least 10 characters.")
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=KDF_ITERATIONS,
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        return Fernet(key)

    def unlock(self, passphrase: str) -> None:
        if self.salt_path.exists():
            salt = self.salt_path.read_bytes()
            if len(salt) != 16:
                raise PrivateMemoryError("Private-memory salt file is invalid.")
        else:
            import os
            salt = os.urandom(16)
            self.salt_path.write_bytes(salt)

        fernet = self._derive_fernet(passphrase, salt)
        if self.vault_path.exists():
            try:
                plaintext = fernet.decrypt(self.vault_path.read_bytes())
                data = json.loads(plaintext.decode("utf-8"))
            except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise PrivateMemoryError("Unable to unlock private memory. The passphrase may be incorrect or the vault may be damaged.") from error
            if not isinstance(data, dict) or int(data.get("version", 0)) != VAULT_VERSION:
                raise PrivateMemoryError("Private-memory vault format is unsupported.")
        else:
            data = {"version": VAULT_VERSION, "next_id": 1, "records": []}

        self._fernet = fernet
        self._data = data
        if not self.vault_path.exists():
            self._save()

    def lock(self) -> None:
        self._fernet = None
        self._data = None

    def _require_unlocked(self) -> tuple[Fernet, dict[str, Any]]:
        if self._fernet is None or self._data is None:
            raise PrivateMemoryLockedError("Private memory is locked. Use /private-memory unlock.")
        return self._fernet, self._data

    def _save(self) -> None:
        fernet, data = self._require_unlocked()
        plaintext = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encrypted = fernet.encrypt(plaintext)
        temporary = self.vault_path.with_suffix(self.vault_path.suffix + ".tmp")
        temporary.write_bytes(encrypted)
        temporary.replace(self.vault_path)

    @staticmethod
    def _validate_category(category: str) -> str:
        cleaned = category.strip().lower()
        if cleaned not in ALLOWED_CATEGORIES:
            raise ValueError("Invalid category. Use: " + ", ".join(sorted(ALLOWED_CATEGORIES)))
        return cleaned

    @staticmethod
    def _validate_policy(policy: str) -> str:
        cleaned = policy.strip().lower()
        if cleaned not in ALLOWED_PRIVATE_POLICIES:
            raise ValueError("Sensitive-memory policy must be explicit_only or never_prompt.")
        return cleaned

    def add(self, *, content: str, category: str, retrieval_policy: str = "explicit_only", expires_at: str | None = None) -> str:
        _, data = self._require_unlocked()
        cleaned_content = " ".join(content.strip().split())
        if not cleaned_content:
            raise ValueError("Sensitive memory content cannot be empty.")
        if len(cleaned_content) > 1000:
            raise ValueError("Sensitive memory content exceeds 1000 characters.")
        cleaned_category = self._validate_category(category)
        cleaned_policy = self._validate_policy(retrieval_policy)
        cleaned_expiration = self._normalize_expiration(expires_at)
        for record in data["records"]:
            if str(record["content"]).casefold() == cleaned_content.casefold():
                raise ValueError("An identical sensitive memory already exists.")
        number = int(data["next_id"])
        data["next_id"] = number + 1
        now = self._utc_now()
        record = {
            "id": f"S{number}",
            "content": cleaned_content,
            "category": cleaned_category,
            "retrieval_policy": cleaned_policy,
            "expires_at": cleaned_expiration,
            "created_at": now,
            "updated_at": now,
        }
        data["records"].append(record)
        self._save()
        return str(record["id"])

    def _rows(self, *, include_expired: bool = False) -> list[dict[str, Any]]:
        _, data = self._require_unlocked()
        rows = [dict(record) for record in data.get("records", [])]
        if not include_expired:
            rows = [row for row in rows if not self._is_expired(row.get("expires_at"))]
        return rows

    def list_all(self, *, include_expired: bool = True) -> list[PrivateMemoryRecord]:
        return [PrivateMemoryRecord(**row) for row in self._rows(include_expired=include_expired)]

    def get(self, memory_id: str) -> PrivateMemoryRecord | None:
        normalized = memory_id.strip().upper()
        for row in self._rows(include_expired=True):
            if str(row["id"]).upper() == normalized:
                return PrivateMemoryRecord(**row)
        return None

    @staticmethod
    def _tokens(text: str) -> set[str]:
        stop = {"the", "and", "for", "with", "that", "this", "user", "from", "about", "what", "when", "where", "how"}
        return {token for token in re.findall(r"[a-z0-9+#.-]+", text.lower()) if len(token) > 1 and token not in stop}

    def search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in self._rows(include_expired=False):
            if row["retrieval_policy"] != "explicit_only":
                continue
            content_tokens = self._tokens(str(row["content"]))
            matched = sorted(query_tokens & content_tokens)
            if not matched:
                continue
            coverage = len(matched) / len(query_tokens)
            score = float(len(matched)) + coverage * 4.0
            item = dict(row)
            item["relevance_score"] = score
            item["matched_terms"] = matched
            item["retrieval_reason"] = "Explicit private-memory search matched: " + ", ".join(matched)
            scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], pair[1]["id"]), reverse=True)
        return [item for _, item in scored[:max(1, min(int(top_k), 20))]]

    def update(self, memory_id: str, *, content: str) -> bool:
        _, data = self._require_unlocked()
        normalized = memory_id.strip().upper()
        cleaned = " ".join(content.strip().split())
        if not cleaned:
            raise ValueError("Sensitive memory content cannot be empty.")
        for record in data["records"]:
            if str(record["id"]).upper() == normalized:
                record["content"] = cleaned
                record["updated_at"] = self._utc_now()
                self._save()
                return True
        return False

    def set_policy(self, memory_id: str, retrieval_policy: str) -> bool:
        _, data = self._require_unlocked()
        cleaned = self._validate_policy(retrieval_policy)
        normalized = memory_id.strip().upper()
        for record in data["records"]:
            if str(record["id"]).upper() == normalized:
                record["retrieval_policy"] = cleaned
                record["updated_at"] = self._utc_now()
                self._save()
                return True
        return False

    def set_expiration(self, memory_id: str, expires_at: str | None) -> bool:
        _, data = self._require_unlocked()
        cleaned = self._normalize_expiration(expires_at)
        normalized = memory_id.strip().upper()
        for record in data["records"]:
            if str(record["id"]).upper() == normalized:
                record["expires_at"] = cleaned
                record["updated_at"] = self._utc_now()
                self._save()
                return True
        return False

    def delete(self, memory_id: str) -> bool:
        _, data = self._require_unlocked()
        normalized = memory_id.strip().upper()
        before = len(data["records"])
        data["records"] = [record for record in data["records"] if str(record["id"]).upper() != normalized]
        if len(data["records"]) == before:
            return False
        self._save()
        return True
