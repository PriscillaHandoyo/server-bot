# Checking functions. All return CheckResult so the format all the same.

import asyncio
from dataclasses import dataclass

import httpx


@dataclass
class CheckResult:
    name:str #ex., "SGE"
    ok:bool #true if its healty
    detail:str #brief explanation to be displayed on Telegram

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
