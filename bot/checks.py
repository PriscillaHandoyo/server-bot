# Checking functions. All return CheckResult so the format all the same.

import asyncio
import json
import shutil
from dataclasses import dataclass

import httpx


@dataclass
class CheckResult:
    name:str #ex., "SGE"
    ok:bool #true if its healty
    detail:str #brief explanation to be displayed on Telegram

# Helper
async def run_command(*args: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Execute shell commands WITHOUT freezing the bot.
    
        Use `create_subprocess_exec` (instead of `subprocess.run`)
        so that the bot can continue responding to other messages while waiting for
        the command to finish

        Returns `(exit_code, stdout, stderr)`
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
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
        return 124, "", f"timeout after {timeout:.0f} seconds"

    return proc.returncode, out.decode().strip(), err.decode().strip()

# Website
# Meaning of error statuses that frequently appear via Cloudflare Tunnel
STATUS_MEANING = {
    500: "Error within the application (backend crash)",
    502: "Bad gateway: the tunnel is active, but the application is not responding",
    503: "Application is currently unavailable",
    504: "Gateway timeout: the application took too long to respond",
    521: "Destination server refused the connection",
    522: "Connection to the server timed out",
    530: "Cloudflare tunnel disconnected/server not connected",
}

async def check_site(name: str, url:str, timeout: float = 10.0) -> CheckResult:
    # Open one website and report the results
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return CheckResult(name, False, f"Timeout, no response within {timeout:.0f} seconds")
    except httpx.RequestError as e:
        # DNS failure, internet outage, certificate issues, etc
        return CheckResult(name, False, f"Can't connect ({e.__class__.__name__})")

    code = response.status_code
    if code < 400:
        ms = response.elapsed.total_seconds() * 1000
        return CheckResult(name, True, f"OK ({code}, {ms:.0f} ms)")

    meaning = STATUS_MEANING.get(code, "Error")
    return CheckResult(name, False, f"{code}: {meaning}")


async def check_sites(sites: dict[str, str]) -> list[CheckResult]:
    # Check multiple websites at once (in parallel, so you don't have to wait for them one by one)
    return await asyncio.gather(*(check_site(name, url) for name, url in sites.items()))

# Disk
def check_disk(path: str = "/", limit_percent: float = 85.0) -> CheckResult:
    # Check the percentage of disk space used. No need for async, as it's instantaneous
    try:
        usage = shutil.disk_usage(path)
    except OSError as e:
        return CheckResult("Disk", False, f"Can't read: {e}")

    percent = usage.used / usage.total * 100
    free_gb = usage.free / 1024**3
    detail = f"{percent:.0f}% used, {free_gb:.1f} GB remaining"
    return CheckResult("Disk", percent < limit_percent, detail)

# Container
async def check_containers(names: list[str]) -> list[CheckResult]:
    # Ensure all containers in `names` are running
    if not names:
        return []

    code, out, err = await run_command("docker", "ps", "--format", "{{.Names}}")
    if code != 0:
        return [CheckResult("Docker", False, f"Can't check container: {err or code}")]

    running = set(out.splitlines())
    return [
        CheckResult(name, name in running, "running" if name in running else "DEAD / can't be found")
        for name in names
    ]

# Tunnel
async def check_tunnel_connectors(tunnel_id: str) -> CheckResult:
    """Each tunnel must have exactly one connector.

        0 connectors = the tunnel is broken; the website becomes inaccessible.
        2+ connectors = another machine has also connected (like the Mac case we saw recently),
        causing requests tp be split, with some failing intermittently.
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
        return CheckResult(f"Tunnel {short_id}", False, "The cloudflared output is unreadable")

    name = data.get("name") or short_id
    connectors = data.get("conns") or []
    count = len(connectors)

    if count == 1:
        return CheckResult(f"Tunnel {name}", True, "1 connector")
    if count == 0:
        return CheckResult(f"Tunnel {name}", False, "0 connector: tunnel PUTUS")

    archs = ", ".join(c.get("arch", "?") for c in connectors)
    return CheckResult(f"Tunnel {name}", False, f"{count} connector ({archs}): another machine is connected too!")

async def check_tunnels(tunnel_ids: list[str]) -> list[CheckResult]:
    return await asyncio.gather(*(check_tunnel_connectors(t) for t in tunnel_ids))
