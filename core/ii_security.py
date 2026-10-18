"""Security helpers for II v3.0: private mode, API key, SSRF guard, code execution.

Design rules
------------
* ``II_PRIVATE`` is read *dynamically* (not cached at import time) so tests and
  operators can toggle it without reloading modules.  Truthy values:
  ``1, true, yes, on`` (case-insensitive).  When enabled, every network-egress
  tool (web search, URL fetch, research, repo ingestion) is refused.
* ``validate_public_url`` is the single SSRF gate for user/model supplied URLs.
  It only allows ``http``/``https``, rejects embedded credentials, loopback /
  private / link-local / reserved / multicast targets, ``localhost`` and
  metadata hostnames, and resolves hostnames and rejects them if *any*
  resolved address is non-public.  DNS resolution is a best-effort check at
  validation time (a TOCTOU / DNS-rebinding window remains between validation
  and connect; see docs/SECURITY.md).
* ``run_python_code`` is the single implementation behind every
  ``execute_code`` tool.  Modes (``II_CODE_SANDBOX``):

  - ``subprocess`` (default): temp-dir subprocess with a scrubbed
    environment, timeout, output cap, and POSIX resource limits where
    available.  This is **not** an OS sandbox -- code still runs as the
    current user.
  - ``docker``: fail-closed Docker execution (``--network none``,
    ``--read-only``, dropped capabilities, memory/CPU/pid limits).  If Docker
    or the image is unavailable the call fails; it never silently falls back.

  ``II_REQUIRE_SANDBOX=1`` refuses the ``subprocess`` mode entirely.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlparse

_TRUTHY = {"1", "true", "yes", "on"}

PRIVATE_MODE_MESSAGE = (
    "Blocked by II_PRIVATE mode: network tools (search_web, read_url, "
    "research, repo ingestion) are disabled. Unset II_PRIVATE or set it to "
    "false/0 to re-enable them. Local tools (model chat via a local backend, "
    "blackboard, graph, verification, SMT, synthesis) remain available."
)

_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata.google",
    "instance-data",
}
# Cloud metadata endpoints (defense in depth; already covered by IP checks).
_BLOCKED_IPS = {"169.254.169.254", "fd00:ec2::254", "100.100.100.200"}


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def is_private_mode(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Return True when II_PRIVATE requests no-network operation."""
    env = os.environ if environ is None else environ
    return _truthy(env.get("II_PRIVATE"))


def web_blocked_message(feature: str = "web access") -> str:
    return f"{PRIVATE_MODE_MESSAGE} (blocked feature: {feature})"


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if str(ip) in _BLOCKED_IPS:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(url: str, *, resolver=None) -> str:
    """Validate a user/model supplied URL for safe server-side fetching.

    Returns the normalized URL (fragment removed).  Raises ``ValueError``
    with an operator-readable reason for anything that is not a public
    http(s) URL.  ``resolver`` is injectable for tests; it must behave like
    ``socket.getaddrinfo``.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string.")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// URLs may be fetched.")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed.")
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise ValueError("URL has no host.")
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise ValueError(f"Host {host!r} is blocked (local/internal host).")

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        literal = ipaddress.ip_address(host)
        addresses.append(literal)
    except ValueError:
        resolve = resolver or socket.getaddrinfo
        try:
            infos = resolve(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                            proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise ValueError(f"Host {host!r} could not be resolved: {exc}") from exc
        for info in infos or []:
            sockaddr = info[4]
            if not sockaddr:
                continue
            try:
                addresses.append(ipaddress.ip_address(sockaddr[0]))
            except ValueError:
                continue
        if not addresses:
            raise ValueError(f"Host {host!r} resolved to no addresses.")

    for address in addresses:
        if _ip_is_blocked(address):
            raise ValueError(
                f"Host {host!r} resolves to non-public address {address}; "
                "fetching private/loopback/link-local targets is blocked (SSRF guard)."
            )
    # Rebuild without fragment (fragments are never sent to servers anyway).
    return parsed._replace(fragment="").geturl()


def is_public_url(url: str, **kwargs) -> bool:
    try:
        validate_public_url(url, **kwargs)
        return True
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# Code execution
# --------------------------------------------------------------------------- #

def sandbox_mode(environ: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    mode = (env.get("II_CODE_SANDBOX") or "subprocess").strip().lower()
    if mode in {"local", "none", "subprocess"}:
        return "subprocess"
    if mode == "docker":
        return "docker"
    return f"invalid:{mode}"


def _posix_limits(timeout: int):
    """preexec_fn applying POSIX resource limits (best effort, POSIX only)."""
    def _apply() -> None:  # pragma: no cover - exercised only on POSIX
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (max(1, timeout + 1), max(2, timeout + 2)))
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
            except (ValueError, OSError):
                pass
        except Exception:
            pass

    return _apply if os.name == "posix" else None


def _cap_output(text: str, limit: int = 20_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[output truncated]"


def _run_docker(code: str, timeout: int, environ: Mapping[str, str]) -> tuple[bool, str]:
    docker = shutil.which("docker")
    if not docker:
        return False, (
            "II_CODE_SANDBOX=docker but no `docker` executable was found. "
            "Failing closed; refusing to fall back to unsandboxed execution."
        )
    image = (environ.get("II_SANDBOX_IMAGE") or "python:3.11-slim").strip()
    command = [
        docker, "run", "--rm", "-i",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "256m", "--memory-swap", "256m",
        "--cpus", "0.5", "--pids-limit", "64",
        "--user", "65534:65534",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "-w", "/tmp",
        image, "python", "-I", "-",
    ]
    try:
        result = subprocess.run(
            command, input=code, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "Execution timed out (possible infinite loop). [docker sandbox]"
    except OSError as exc:
        return False, f"Docker sandbox failed to start: {exc}"
    output = result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
    return result.returncode == 0, _cap_output(output)


def run_python_code(code: str, timeout: int = 20) -> tuple[bool, str]:
    """Run Python code under the configured execution mode.

    Returns ``(success, output)``.  See module docstring for modes; the
    default subprocess mode is NOT a sandbox.
    """
    if not isinstance(code, str) or not code.strip():
        return False, "No code supplied."
    environ = os.environ
    mode = sandbox_mode(environ)
    if mode.startswith("invalid:"):
        return False, (
            f"Unknown II_CODE_SANDBOX mode {mode.split(':', 1)[1]!r}; "
            "expected 'subprocess' or 'docker'. Failing closed."
        )
    if mode == "docker":
        return _run_docker(code, timeout, environ)
    if _truthy(environ.get("II_REQUIRE_SANDBOX")):
        return False, (
            "II_REQUIRE_SANDBOX=1 requires II_CODE_SANDBOX=docker; "
            "refusing unsandboxed subprocess execution."
        )

    env = {
        "PATH": environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": environ.get("LANG", "C.UTF-8"),
    }
    if os.name == "nt" and environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = environ["SYSTEMROOT"]
    with tempfile.TemporaryDirectory(prefix="ii-exec-") as tmp:
        script = Path(tmp) / "script.py"
        script.write_text(code, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, "-I", str(script)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp,
                env=env,
                preexec_fn=_posix_limits(timeout),  # noqa: S603 - documented mode
            )
        except subprocess.TimeoutExpired:
            return False, "Execution timed out (possible infinite loop)."
        except OSError as exc:
            return False, str(exc)
    output = result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
    return result.returncode == 0, _cap_output(output)


__all__ = [
    "PRIVATE_MODE_MESSAGE",
    "is_private_mode",
    "is_public_url",
    "run_python_code",
    "sandbox_mode",
    "validate_public_url",
    "web_blocked_message",
]
