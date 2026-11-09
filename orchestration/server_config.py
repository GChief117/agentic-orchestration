"""Validate portable server settings before binding a public network interface."""

from urllib.parse import urlsplit


def validate_hosting(mode, host, public_origin, password):
    """Require a canonical origin and strong access secret for hosted operation."""
    if mode == "local":
        if host != "127.0.0.1":
            raise ValueError(
                "Local mode must bind to 127.0.0.1; use hosted mode for network access"
            )
        return None
    if not password or len(password) < 16:
        raise ValueError(
            "Hosted mode requires ACCESS_PASSWORD with at least 16 characters"
        )
    try:
        parsed = urlsplit(public_origin or "")
        parsed.port
    except ValueError as exc:
        raise ValueError("PUBLIC_ORIGIN must be a valid URL") from exc
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in ("http", "https")
        or (
            parsed.scheme != "https"
            and parsed.hostname not in ("127.0.0.1", "localhost")
        )
    ):
        raise ValueError(
            "Set PUBLIC_ORIGIN to the external HTTPS origin (HTTP is allowed only on localhost)"
        )
    return f"{parsed.scheme}://{parsed.netloc}"
