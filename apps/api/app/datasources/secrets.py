"""
Credential resolution for data sources.

``DataSource.secret_ref`` holds a *pointer* to a credential, never the
credential itself. That distinction is the whole point: a connection string in
the database ends up in backups, in query results, in `SELECT *` during
debugging, and in whatever an operator pastes into a ticket. A reference is
useless without the resolver.

Two implementations ship. ``EnvSecretResolver`` reads from the process
environment and is what a small deployment actually uses. ``StaticSecretResolver``
exists for tests. A Vault or AWS Secrets Manager resolver implements the same
two methods; nothing else in the codebase changes.
"""
from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod

# A reference has to be safe to log, so it is constrained to a shape that cannot
# accidentally hold a password: no punctuation a DSN would need.
SECRET_REF_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")


class SecretNotFound(LookupError):
    """Raised with the reference, never with anything resolved."""


class InvalidSecretRef(ValueError):
    pass


def validate_ref(ref: str) -> str:
    # Checked first: a pasted DSN also fails the shape rule, and the generic
    # "must be an identifier" message would leave the user guessing at what they
    # did wrong. The specific diagnosis is the useful one.
    if "://" in ref or "@" in ref:
        raise InvalidSecretRef(
            "That looks like a connection string, not a reference. Store the "
            "DSN in your secret manager and pass its name instead.")
    if not ref or not SECRET_REF_PATTERN.match(ref):
        raise InvalidSecretRef(
            "A secret reference must be a short opaque identifier "
            "(letters, digits, and _ . : / -). It looks like a credential was "
            "passed here directly, which is never stored.")
    return ref


class SecretResolver(ABC):
    @abstractmethod
    def resolve(self, ref: str) -> str:
        """Return the DSN for a reference, or raise SecretNotFound."""

    @abstractmethod
    def exists(self, ref: str) -> bool:
        """Check availability without materialising the secret."""


class EnvSecretResolver(SecretResolver):
    """Resolves ``payments-replica`` to ``$INSIGHTOS_SECRET_PAYMENTS_REPLICA``."""

    def __init__(self, prefix: str = "INSIGHTOS_SECRET_") -> None:
        self.prefix = prefix

    def _key(self, ref: str) -> str:
        return self.prefix + re.sub(r"[^A-Za-z0-9]", "_", validate_ref(ref)).upper()

    def resolve(self, ref: str) -> str:
        value = os.environ.get(self._key(ref))
        if not value:
            raise SecretNotFound(
                f"No credential for '{ref}'. Set {self._key(ref)} in the API's "
                "environment. The value is never written to the database.")
        return value

    def exists(self, ref: str) -> bool:
        return bool(os.environ.get(self._key(ref)))


class StaticSecretResolver(SecretResolver):
    """In-memory resolver for tests and local development."""

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = dict(secrets or {})

    def put(self, ref: str, dsn: str) -> None:
        self._secrets[validate_ref(ref)] = dsn

    def resolve(self, ref: str) -> str:
        try:
            return self._secrets[validate_ref(ref)]
        except KeyError:
            raise SecretNotFound(f"No credential registered for '{ref}'.") from None

    def exists(self, ref: str) -> bool:
        return validate_ref(ref) in self._secrets


_default: SecretResolver = EnvSecretResolver()


def default_resolver() -> SecretResolver:
    return _default


def set_default_resolver(resolver: SecretResolver) -> None:
    global _default
    _default = resolver


def redact(dsn: str) -> str:
    """Mask credentials in a DSN so it can appear in a log line or an error."""
    return re.sub(r"://([^:/@]+)(:[^@]*)?@", lambda m: f"://{m.group(1)}:***@", dsn)
