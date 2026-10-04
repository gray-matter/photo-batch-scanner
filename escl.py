"""Minimal eSCL (AirScan) client: discovers the scanner over mDNS and drives it
over plain HTTP. No vendor driver or SANE backend needed."""
import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx
from zeroconf import ServiceBrowser, Zeroconf

# Called as on_progress(stage, done, total). "scanning" fires once the job is
# submitted and we're waiting on the scanner hardware (no finer-grained signal
# exists for that phase); "transferring" fires repeatedly with byte counts
# (total is None if the response has no Content-Length).
ProgressCallback = Callable[[str, Optional[int], Optional[int]], None]

SCAN_SETTINGS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.9</pwg:Version>
  <pwg:InputSource>Platen</pwg:InputSource>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:XResolution>{resolution}</scan:XResolution>
  <scan:YResolution>{resolution}</scan:YResolution>
</scan:ScanSettings>
"""


class ScannerNotFound(Exception):
    pass


@dataclass
class ScannerInfo:
    name: str
    base_url: str


def _browse_once(timeout: float) -> list[ScannerInfo]:
    found: list[ScannerInfo] = []

    class Listener:
        def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
            info = zc.get_service_info(service_type, name)
            if info is None:
                return
            host = info.server.rstrip(".")
            found.append(ScannerInfo(name=name, base_url=f"http://{host}:{info.port}/eSCL"))

        def update_service(self, *args, **kwargs) -> None:
            pass

        def remove_service(self, *args, **kwargs) -> None:
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, "_uscan._tcp.local.", Listener())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not found:
            time.sleep(0.1)
    finally:
        zc.close()
    return found


def discover_scanner(timeout: float = 6.0, retries: int = 2) -> ScannerInfo:
    """Browse for _uscan._tcp (eSCL) services on the local network.

    mDNS discovery is occasionally slow to respond on the first attempt, so
    this retries a couple of times before giving up.
    """
    for attempt in range(retries + 1):
        found = _browse_once(timeout)
        if found:
            return found[0]
    raise ScannerNotFound("No eSCL (AirScan) scanner found on the local network")


def scan_to_file(
    scanner: ScannerInfo,
    output_path: str,
    resolution: int = 600,
    timeout: float = 60.0,
    on_progress: Optional[ProgressCallback] = None,
) -> None:
    """Trigger a Platen scan and save the resulting JPEG to output_path."""
    settings = SCAN_SETTINGS_TEMPLATE.format(resolution=resolution)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            f"{scanner.base_url}/ScanJobs",
            content=settings,
            headers={"Content-Type": "text/xml"},
        )
        resp.raise_for_status()
        job_url = resp.headers["Location"]

        if on_progress:
            on_progress("scanning", None, None)

        with client.stream("GET", f"{job_url}/NextDocument") as doc_resp:
            doc_resp.raise_for_status()
            total = doc_resp.headers.get("Content-Length")
            total = int(total) if total is not None else None
            done = 0
            with open(output_path, "wb") as f:
                for chunk in doc_resp.iter_bytes():
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress("transferring", done, total)


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "scan.jpg"
    print("Discovering scanner...")
    info = discover_scanner()
    print(f"Found: {info.name} at {info.base_url}")
    print("Scanning...")
    scan_to_file(info, out)
    print(f"Saved to {out}")
