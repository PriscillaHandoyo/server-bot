"""Health checks. Every check returns a CheckResult so the format is uniform."""

import asyncio
import json
import shutil
from dataclasses import dataclass

import httpx

from bot import config


@dataclass
class CheckResult:
    name: str      # e.g. "SGE"
    ok: bool       # True when healthy
    detail: str    # short explanation shown in Telegram


# ---------------------------------------------------------------- helper ---

async def run_command(*args: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Run a shell command WITHOUT freezing the bot.

    Uses create_subprocess_exec (not subprocess.run), so while we wait for
    the command, the bot can still answer other messages.
    Returns (exit_code, stdout, stderr).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,   # never wait for keyboard input
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", f"command '{args[0]}' not found"

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"timed out after {timeout:g} seconds"

    return proc.returncode, out.decode().strip(), err.decode().strip()


# --------------------------------------------------------------- website ---

# Meaning of error codes commonly seen through a Cloudflare Tunnel
STATUS_MEANING = {
    500: "Error inside the app (backend crashed)",
    502: "Bad gateway: tunnel is up, but the app isn't responding",
    503: "App unavailable",
    504: "Gateway timeout: the app took too long to respond",
    521: "Origin server refused the connection",
    522: "Connection to the server timed out",
    530: "Cloudflare tunnel is down / server not connected",
}


async def check_site(name: str, url: str, timeout: float = 10.0) -> CheckResult:
    """Open one website and report the result."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return CheckResult(name, False, f"Timeout, no response within {timeout:g} seconds")
    except httpx.RequestError as e:
        # DNS failure, no internet, certificate problem, etc.
        return CheckResult(name, False, f"Can't connect ({e.__class__.__name__})")

    code = response.status_code
    if code < 400:
        ms = response.elapsed.total_seconds() * 1000
        return CheckResult(name, True, f"OK ({code}, {ms:.0f} ms)")

    meaning = STATUS_MEANING.get(code, "Error")
    return CheckResult(name, False, f"{code}: {meaning}")


async def check_sites(sites: dict[str, str]) -> list[CheckResult]:
    """Check several websites at once (in parallel, not one by one)."""
    return await asyncio.gather(*(check_site(name, url) for name, url in sites.items()))


# ------------------------------------------------------------------ disk ---

def check_disk(path: str = "/", limit_percent: float = 85.0) -> CheckResult:
    """Check how full the disk is. Not async because it's instant."""
    try:
        usage = shutil.disk_usage(path)
    except OSError as e:
        return CheckResult("Disk", False, f"Can't read: {e}")

    percent = usage.used / usage.total * 100
    free_gb = usage.free / 1024**3
    detail = f"{percent:.0f}% used, {free_gb:.1f} GB remaining"
    return CheckResult("Disk", percent < limit_percent, detail)


# ------------------------------------------------------------- container ---

async def check_containers(names: list[str]) -> list[CheckResult]:
    """Make sure every container in `names` is running."""
    if not names:
        return []

    code, out, err = await run_command("docker", "ps", "--format", "{{.Names}}")
    if code != 0:
        return [CheckResult("Docker", False, f"Can't list containers: {err or code}")]

    running = set(out.splitlines())
    return [
        CheckResult(name, name in running, "running" if name in running else "DOWN / not found")
        for name in names
    ]


# ---------------------------------------------------------------- tunnel ---

async def check_tunnel_connectors(tunnel_id: str) -> CheckResult:
    """A tunnel must have exactly 1 connector.

    0 connectors  = tunnel is down, websites unreachable.
    2+ connectors = another machine joined the tunnel (like the Mac incident),
                    requests get split and some fail at random.
    """
    short_id = tunnel_id[:8]
    code, out, err = await run_command(
        "cloudflared", "tunnel", "info", "--output", "json", tunnel_id
    )
    if code != 0:
        return CheckResult(f"Tunnel {short_id}", False, f"Can't check: {err[:200] or code}")

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return CheckResult(f"Tunnel {short_id}", False, "Couldn't parse cloudflared output")

    name = data.get("name") or short_id
    connectors = data.get("conns") or []
    count = len(connectors)

    if count == 1:
        return CheckResult(f"Tunnel {name}", True, "1 connector")
    if count == 0:
        return CheckResult(f"Tunnel {name}", False, "0 connectors: tunnel is DOWN")

    archs = ", ".join(c.get("arch", "?") for c in connectors)
    return CheckResult(
        f"Tunnel {name}", False, f"{count} connectors ({archs}): another machine joined!"
    )


async def check_tunnels(tunnel_ids: list[str]) -> list[CheckResult]:
    return await asyncio.gather(*(check_tunnel_connectors(t) for t in tunnel_ids))


# --------------------------------------------------------------- services ---

async def check_unit(unit: str) -> CheckResult:
    """Did the last run of a systemd service (sync, backup...) succeed?

    Catches jobs that fail silently, like a sync that keeps failing with
    'Permission denied' for days while every website still looks fine.
    """
    code, out, err = await run_command(
        "systemctl", "show", unit,
        "--property=LoadState,ActiveState,Result,ExecMainExitTimestamp",
    )
    if code != 0:
        return CheckResult(unit, False, f"Could not check: {err or code}")

    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if props.get("LoadState") == "not-found":
        return CheckResult(unit, False, "Service not found")

    # "Wed 2026-11-25 11:32:01 WIB" -> "2026-11-25 11:32"
    parts = props.get("ExecMainExitTimestamp", "").split()
    when = f"{parts[1]} {parts[2][:5]}" if len(parts) >= 3 else ""

    state = props.get("ActiveState", "")
    result = props.get("Result", "")
    if state == "failed" or result not in ("", "success"):
        return CheckResult(unit, False, f"FAILED ({result or state})" + (f", last run {when}" if when else ""))
    if state == "activating":
        return CheckResult(unit, True, "running now")
    return CheckResult(unit, True, f"last run OK {when}" if when else "OK (not run yet)")


async def check_units(units: list[str]) -> list[CheckResult]:
    return await asyncio.gather(*(check_unit(u) for u in units))


# ------------------------------------------------------------------- all ---

async def run_all_checks() -> dict[str, list[CheckResult]]:
    """Run every check for this server, grouped by section.

    Used by both /status and the automatic monitor, so the logic lives in one place.
    Empty sections (e.g. no containers configured) are left out.
    """
    sites, containers, tunnels, units = await asyncio.gather(
        check_sites(config.SITES),
        check_containers(config.CONTAINERS),
        check_tunnels(config.TUNNEL_IDS),
        check_units(config.UNITS),
    )
    disk = check_disk(config.DISK_PATH, config.DISK_LIMIT_PERCENT)

    sections = {
        "Websites": list(sites),
        "Containers": containers,
        "Tunnels": list(tunnels),
        "Services": list(units),
        "Server": [disk],
    }
    return {title: results for title, results in sections.items() if results}
