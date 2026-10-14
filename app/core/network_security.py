import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_public_network_guard_active: ContextVar[bool] = ContextVar('public_network_guard_active', default=False)
_original_getaddrinfo = socket.getaddrinfo


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    results = _original_getaddrinfo(host, port, *args, **kwargs)
    if not _public_network_guard_active.get():
        return results

    try:
        addresses = {ipaddress.ip_address(result[4][0]) for result in results}
    except (IndexError, TypeError, ValueError) as exc:
        raise socket.gaierror('Resolved address is invalid.') from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise socket.gaierror('Non-public network addresses are not allowed.')
    return results


socket.getaddrinfo = _guarded_getaddrinfo


@contextmanager
def public_network_dns_guard() -> Iterator[None]:
    token = _public_network_guard_active.set(True)
    try:
        yield
    finally:
        _public_network_guard_active.reset(token)