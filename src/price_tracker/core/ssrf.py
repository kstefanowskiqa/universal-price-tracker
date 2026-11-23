from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse, urlunparse

from price_tracker.core.exceptions import ValidationError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
HostResolver = Callable[[str, int], Awaitable[list[str]]]

ALLOWED_SCHEMES = frozenset({"http", "https"})
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata",
    }
)


class SSRFBlockedError(ValidationError):
    """Wyjątek rzucany, gdy docelowy adres URL wskazuje na niedozwolony host lub prywatne IP."""


def normalize_url(raw_url: str) -> str:
    """Waliduje składnię URL i zwraca jego znormalizowaną postać bez fragmentu."""
    candidate = raw_url.strip()
    if not candidate:
        raise ValidationError("Adres URL nie może być pusty.")

    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFBlockedError(f"Niedozwolony schemat URL: '{scheme or '(brak)'}'.")

    if not parsed.hostname:
        raise ValidationError("Adres URL musi zawierać poprawną nazwę hosta.")

    if parsed.username or parsed.password:
        raise SSRFBlockedError("Adres URL nie może zawierać poświadczeń (user:password@host).")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("Nieprawidłowy numer portu w adresie URL.") from exc

    host = parsed.hostname.lower().rstrip(".")
    netloc = host
    if ":" in host:  # IPv6 literal
        netloc = f"[{host}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"

    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def extract_domain(url: str) -> str:
    """Zwraca znormalizowaną domenę hosta bez prefiksu 'www.'."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def is_ip_public(ip_str: str) -> bool:
    """Zwraca True wyłącznie dla globalnie routowalnych, publicznych adresów IP."""
    try:
        ip_obj: IPAddress = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    return bool(
        ip_obj.is_global
        and not ip_obj.is_private
        and not ip_obj.is_loopback
        and not ip_obj.is_link_local
        and not ip_obj.is_multicast
        and not ip_obj.is_reserved
        and not ip_obj.is_unspecified
    )


async def default_host_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            ip = str(sockaddr[0])
            if ip not in ips:
                ips.append(ip)
    return ips


async def validate_url_ssrf(
    raw_url: str,
    *,
    resolver: HostResolver | None = None,
) -> str:
    """Sprawdza URL pod kątem SSRF (schemat, nazwa hosta i wszystkie rozwiązane adresy IP)."""
    normalized = normalize_url(raw_url)
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower().rstrip(".")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if host in BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise SSRFBlockedError(f"Zablokowano niedozwolony host lokalny/wewnętrzny: '{host}'.")

    # Przypadek 1: URL zawiera bezpośrednio adres IP
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_ip_public(host):
            raise SSRFBlockedError(f"Zablokowano prywatny lub lokalny adres IP: '{host}'.")
        return normalized

    # Przypadek 2: Rozwiązanie DNS i weryfikacja wszystkich rekordów A/AAAA
    resolve_fn = resolver or default_host_resolver
    try:
        resolved_ips = await resolve_fn(host, port)
    except OSError as exc:
        raise ValidationError(f"Nie udało się rozwiązać nazwy DNS dla '{host}': {exc}") from exc

    if not resolved_ips:
        raise ValidationError(f"Brak rekordów DNS dla hosta '{host}'.")

    for ip in resolved_ips:
        if not is_ip_public(ip):
            raise SSRFBlockedError(
                f"Host '{host}' rozwiązuje się na niedozwolony prywatny adres IP ({ip})."
            )

    return normalized
