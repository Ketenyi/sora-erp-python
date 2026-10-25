#!/usr/bin/env python3
"""SORA endpoint discovery agent.

Collects hardware, storage, network, port, security and installed-software
telemetry on Windows, Linux and macOS, then reports it to a SORA ERP workspace
over HTTPS using a hardware-bound access token.

Only the Python standard library is required. Typical use::

    python sora_asset_agent.py --config sora-agent.json --daemon

The bootstrap token embedded in the package is exchanged once for a
hardware-bound JWT, stored in a protected state file and refreshed before it
expires. Inventory batches are compressed with Zstandard where available and
fall back to gzip on older Python runtimes.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import getpass
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

AGENT_VERSION = "1.1.0"
DEFAULT_HEARTBEAT = 60
DEFAULT_INVENTORY = 1800
DEFAULT_INTERVAL = DEFAULT_INVENTORY
MIN_INTERVAL = 30
DEFAULT_SOFTWARE_LIMIT = 2000
MAX_PORTS = 256
USER_AGENT = f"SORA-Inventory-Agent/{AGENT_VERSION}"
WINDOWS = sys.platform.startswith("win")
LINUX = sys.platform.startswith("linux")
MACOS = sys.platform == "darwin"

try:  # Python 3.14 standard library
    from compression import zstd  # type: ignore
except ImportError:  # pragma: no cover - Python < 3.14
    zstd = None


def log(message: str) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[warning] {message}", file=sys.stderr, flush=True)


class AgentHTTPError(RuntimeError):
    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def run_command(args, timeout: int = 25) -> str:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if WINDOWS else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def run_powershell(script: str, timeout: int = 30) -> str:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        return ""
    return run_command(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout
    )


def parse_json(text: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def clean_hostname() -> str:
    try:
        return socket.gethostname()[:200]
    except OSError:
        return platform.node()[:200] or "unknown-host"


def safe_machine_id(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._:-]", "", str(raw or ""))
    if len(value) >= 8:
        return value[:128]
    seed = f"{platform.node()}:{uuid.getnode()}:{platform.platform()}"
    return hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()


def hardware_fingerprint(machine_id: str, system: dict, cpu: dict) -> str:
    material = "|".join(
        [
            machine_id,
            str(system.get("serial_number") or ""),
            str(system.get("uuid") or ""),
            str(system.get("bios_serial") or ""),
            str(system.get("manufacturer") or ""),
            str(system.get("model") or ""),
            str(cpu.get("model") or ""),
            str(platform.machine()),
        ]
    )
    return hashlib.sha256(f"sora-fingerprint::{material}".encode("utf-8", "replace")).hexdigest()


def normalize_mac(value: str) -> str:
    raw = re.sub(r"[^0-9A-Fa-f]", "", str(value or ""))
    if len(raw) not in {12, 16}:
        return ""
    return ":".join(raw[index : index + 2] for index in range(0, len(raw), 2)).upper()


def _add_port(rows: list, seen: set, protocol: str, address: str, port, process: str = "", state: str = "LISTEN") -> None:
    try:
        number = int(str(port).split("/")[0])
    except (TypeError, ValueError):
        return
    if not 0 < number <= 65535:
        return
    key = (protocol.lower(), str(address or "")[:80], number, str(process or "")[:200])
    if key in seen or len(rows) >= MAX_PORTS:
        return
    seen.add(key)
    rows.append(
        {
            "protocol": protocol.lower(),
            "local_address": str(address or "")[:80],
            "port": number,
            "process": str(process or "")[:200],
            "state": str(state or "")[:20],
        }
    )


def parse_netstat_ports(output: str) -> list:
    rows = []
    seen = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        proto = parts[0].lower()
        if proto not in {"tcp", "tcp6", "udp", "udp6"}:
            continue
        is_udp = proto.startswith("udp")
        if WINDOWS:
            local = parts[1]
            state = parts[-2] if len(parts) >= 4 else "LISTENING"
            process = parts[-1] if parts[-1].isdigit() else ""
        else:
            local = parts[3] if len(parts) > 3 else parts[1]
            state = parts[-2] if len(parts) >= 2 else "LISTEN"
            process = ""
            if "/" in parts[-1]:
                process = parts[-1].split("/", 1)[1]
        if ":" not in local:
            continue
        address, _, port = local.rpartition(":")
        if not is_udp and state.upper() not in {"LISTEN", "LISTENING"}:
            continue
        _add_port(
            rows,
            seen,
            "tcp" if proto.startswith("tcp") else "udp",
            address.strip("[]"),
            port,
            process,
            "UNCONN" if is_udp else "LISTEN",
        )
    return rows


def parse_ss_ports(output: str) -> list:
    rows = []
    seen = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        proto = parts[0].lower()
        if proto not in {"tcp", "udp", "tcp6", "udp6"}:
            continue
        if str(parts[1]).upper() not in {"LISTEN", "UNCONN"}:
            continue
        local = parts[3]
        process = ""
        match = re.search(r'users:\(\("([^"]+)"', line)
        if match:
            process = match.group(1)
        address, _, port = local.rpartition(":")
        _add_port(
            rows,
            seen,
            "tcp" if proto.startswith("tcp") else "udp",
            address.strip("[]"),
            port,
            process,
            str(parts[1]).upper(),
        )
    return rows


def parse_lsof_ports(output: str) -> list:
    rows = []
    seen = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 9 or ":" not in parts[8]:
            continue
        local = parts[8]
        address, _, port = local.rpartition(":")
        protocol = "tcp" if "TCP" in line.upper() else "udp"
        _add_port(rows, seen, protocol, address.strip("[]"), port, parts[0], "LISTEN")
    return rows


def _first_content_line(path: str) -> str:
    for line in read_text(path).splitlines():
        value = line.strip()
        if value:
            return value
    return ""


def _clean_serial(value) -> str:
    text = str(value or "").strip().strip('"')
    if text.upper() in {
        "",
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "DEFAULT STRING",
        "TO BE FILLED BY O.E.M.",
        "TO BE FILLED BY OEM",
        "SYSTEM SERIAL NUMBER",
    }:
        return ""
    return text[:120]


_BUS_TYPES = {
    0: "unknown",
    1: "scsi",
    2: "atapi",
    3: "ata",
    4: "1394",
    5: "ssa",
    6: "fibre",
    7: "usb",
    8: "raid",
    9: "iscsi",
    10: "sas",
    11: "sata",
    12: "sd",
    13: "mmc",
    14: "virtual",
    15: "filebackedvirtual",
    16: "spaces",
    17: "nvme",
    18: "scm",
    19: "ublock",
}
_MEDIA_TYPES = {0: "", 3: "hdd", 4: "ssd", 5: "scm"}


def _enum_name(value, mapping: dict) -> str:
    try:
        return mapping.get(int(value), "")
    except (TypeError, ValueError):
        return str(value or "").strip().lower()


def disk_type(bus_type=None, media_type=None) -> str:
    bus = _enum_name(bus_type, _BUS_TYPES)
    media = _enum_name(media_type, _MEDIA_TYPES)
    if "nvme" in bus:
        return "nvme"
    if media in {"ssd", "scm", "hdd"}:
        return media
    for candidate in (media, bus):
        for kind in ("nvme", "ssd", "hdd"):
            if kind in candidate:
                return kind
    return ""


def _efi_secure_boot():
    if not os.path.isdir("/sys/firmware/efi"):
        return None
    try:
        for name in os.listdir("/sys/firmware/efi/efivars"):
            if name.startswith("SecureBoot-"):
                with open(os.path.join("/sys/firmware/efi/efivars", name), "rb") as handle:
                    data = handle.read(8)
                return bool(data[4]) if len(data) >= 5 else None
    except OSError:
        return None
    return None


def _proc_process_names() -> set:
    names = set()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return names
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm", "r", encoding="utf-8", errors="replace") as handle:
                name = handle.read(64).strip().lower()
        except OSError:
            continue
        if name:
            names.add(name)
    return names


AV_SIGNATURES = (
    ("clamd", "ClamAV", "Cisco Talos"),
    ("freshclam", "ClamAV", "Cisco Talos"),
    ("sophos", "Sophos Anti-Virus", "Sophos"),
    ("esets", "ESET Server Security", "ESET"),
    ("bdsecd", "Bitdefender Endpoint Security", "Bitdefender"),
    ("kavfs", "Kaspersky Endpoint Security", "Kaspersky"),
    ("masvc", "McAfee Endpoint Security", "McAfee"),
    ("wdavdaemon", "Microsoft Defender Antivirus", "Microsoft"),
)

EDR_SIGNATURES = (
    ("falcon-sensor", "CrowdStrike Falcon"),
    ("crowdstrike", "CrowdStrike Falcon"),
    ("sentinel", "SentinelOne"),
    ("cbdaemon", "VMware Carbon Black"),
    ("cbagent", "VMware Carbon Black"),
    ("carbonblack", "VMware Carbon Black"),
    ("cylance", "BlackBerry CylancePROTECT"),
    ("cyanopro", "BlackBerry CylancePROTECT"),
    ("tanium", "Tanium"),
    ("wazuh", "Wazuh"),
    ("osquery", "osquery"),
    ("mdatp", "Microsoft Defender for Endpoint"),
    ("wdavdaemon", "Microsoft Defender for Endpoint"),
    ("cybereason", "Cybereason"),
    ("huntress", "Huntress"),
    ("elastic-endgame", "Elastic Endgame"),
    ("qualys", "Qualys Cloud Agent"),
    ("rapid7", "Rapid7 Insight Agent"),
)


def security_from_processes(names: set, *, antivirus_up_to_date=None) -> dict:
    antivirus = []
    seen_av = set()
    for token, display, provider in AV_SIGNATURES:
        if display in seen_av:
            continue
        if any(token in name for name in names):
            seen_av.add(display)
            antivirus.append(
                {
                    "name": display,
                    "provider": provider,
                    "enabled": True,
                    "up_to_date": antivirus_up_to_date
                    if display == "ClamAV"
                    else None,
                }
            )
    edr = []
    seen_edr = set()
    for token, display in EDR_SIGNATURES:
        if display in seen_edr:
            continue
        if any(token in name for name in names):
            seen_edr.add(display)
            edr.append({"name": display, "status": "running", "version": ""})
    return {"antivirus": antivirus, "edr": edr}


def clamav_up_to_date() -> bool | None:
    newest = 0.0
    for folder in ("/var/lib/clamav", "/var/lib/clamav-unofficial-sigs"):
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith((".cvd", ".cld", ".hdb", ".ndb")):
                continue
            try:
                newest = max(newest, os.path.getmtime(os.path.join(folder, entry)))
            except OSError:
                continue
    if not newest:
        return None
    return (time.time() - newest) < 7 * 24 * 3600


# ---------------------------------------------------------------------------
# Windows collectors
# ---------------------------------------------------------------------------
def windows_machine_id() -> str:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return safe_machine_id(value)
    except Exception:
        return safe_machine_id("")


def windows_os() -> dict:
    name = "Windows"
    version = platform.release()
    build = platform.version()
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        ) as key:
            product, _ = winreg.QueryValueEx(key, "ProductName")
            name = product
            try:
                display, _ = winreg.QueryValueEx(key, "DisplayVersion")
                if display:
                    version = display
            except OSError:
                pass
            try:
                current_build, _ = winreg.QueryValueEx(key, "CurrentBuildNumber")
                try:
                    revision, _ = winreg.QueryValueEx(key, "UBR")
                    build = f"{current_build}.{revision}"
                except OSError:
                    build = str(current_build)
            except OSError:
                pass
    except Exception:
        pass
    return {
        "family": "windows",
        "name": name,
        "version": version,
        "build": build,
        "architecture": platform.machine(),
        "boot_time": "",
    }


def windows_logged_in_user() -> str:
    output = run_command(["quser"], timeout=10)
    for line in output.splitlines()[1:]:
        parts = line.replace(">", " ").split()
        if len(parts) >= 3 and parts[2].isdigit() and parts[0].upper() != "SYSTEM":
            return parts[0][:150]
    owner = run_powershell(
        "$p = Get-CimInstance Win32_Process -Filter \"Name='explorer.exe'\" | "
        "Select-Object -First 1; if ($p) { $o = Invoke-CimMethod -InputObject $p "
        "-MethodName GetOwner; \"$($o.Domain)\\$($o.User)\" }"
    )
    if owner.strip():
        return owner.strip().lstrip("\\")[:150]
    return (os.environ.get("USERNAME", "") or getpass.getuser())[:150]


def windows_system() -> dict:
    system = {
        "manufacturer": "",
        "model": "",
        "serial_number": "",
        "uuid": "",
        "bios_version": "",
        "bios_serial": "",
        "domain": os.environ.get("USERDOMAIN", ""),
        "logged_in_user": "",
    }
    data = parse_json(
        run_powershell(
            "$cs = Get-CimInstance Win32_ComputerSystem; "
            "$bios = Get-CimInstance Win32_BIOS; "
            "$prod = Get-CimInstance Win32_ComputerSystemProduct; "
            "[pscustomobject]@{manufacturer=$cs.Manufacturer; model=$cs.Model; "
            "domain=$cs.Domain; serial=$bios.SerialNumber; uuid=$prod.UUID; "
            "bios_version=$bios.SMBIOSBIOSVersion} | "
            "ConvertTo-Json -Compress"
        )
    )
    if isinstance(data, dict):
        system["manufacturer"] = data.get("manufacturer") or ""
        system["model"] = data.get("model") or ""
        system["serial_number"] = _clean_serial(data.get("serial"))
        system["bios_serial"] = _clean_serial(data.get("serial"))
        system["uuid"] = _clean_serial(data.get("uuid"))
        system["bios_version"] = str(data.get("bios_version") or "")[:120]
        system["domain"] = data.get("domain") or system["domain"]
    system["logged_in_user"] = windows_logged_in_user()
    return system


def windows_cpu() -> dict:
    cpu = {
        "model": platform.processor() or "",
        "architecture": platform.machine(),
        "physical_cores": None,
        "logical_cores": os.cpu_count(),
        "max_clock_mhz": None,
    }
    data = as_list(
        parse_json(
            run_powershell(
                "Get-CimInstance Win32_Processor | Select-Object Name, "
                "NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed | "
                "ConvertTo-Json -Compress"
            )
        )
    )
    if data:
        first = data[0]
        cpu["model"] = first.get("Name") or cpu["model"]
        cpu["physical_cores"] = first.get("NumberOfCores")
        cpu["logical_cores"] = first.get("NumberOfLogicalProcessors") or cpu["logical_cores"]
        cpu["max_clock_mhz"] = first.get("MaxClockSpeed")
    return cpu


def windows_memory() -> dict:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return {
            "total_mb": int(status.ullTotalPhys / (1024 * 1024)),
            "available_mb": int(status.ullAvailPhys / (1024 * 1024)),
        }
    except Exception:
        return {"total_mb": None, "available_mb": None}


def windows_disks() -> list:
    disks = []
    data = as_list(
        parse_json(
            run_powershell(
                "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | "
                "ForEach-Object { $d = $_; $disk = $null; "
                "try { $disk = Get-Partition -DriveLetter $d.DeviceID.Substring(0,1) "
                "-ErrorAction Stop | Get-Disk -ErrorAction Stop } catch { }; "
                "[pscustomobject]@{DeviceID=$d.DeviceID; VolumeName=$d.VolumeName; "
                "FileSystem=$d.FileSystem; Size=$d.Size; FreeSpace=$d.FreeSpace; "
                "Serial=$disk.SerialNumber; Model=$disk.FriendlyName; "
                "MediaType=$disk.MediaType; BusType=$disk.BusType} } | "
                "ConvertTo-Json -Compress"
            )
        )
    )
    if not data:
        data = as_list(
            parse_json(
                run_powershell(
                    "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | "
                    "Select-Object DeviceID, VolumeName, FileSystem, Size, FreeSpace | "
                    "ConvertTo-Json -Compress"
                )
            )
        )
    for row in data:
        try:
            total = int(row.get("Size") or 0)
            free = int(row.get("FreeSpace") or 0)
        except (TypeError, ValueError):
            continue
        if not total:
            continue
        disks.append(
            {
                "device": row.get("DeviceID") or "",
                "mountpoint": row.get("VolumeName") or row.get("DeviceID") or "",
                "filesystem": row.get("FileSystem") or "",
                "serial": _clean_serial(row.get("Serial")),
                "model": str(row.get("Model") or "").strip()[:120],
                "disk_type": disk_type(row.get("BusType"), row.get("MediaType")),
                "total_gb": round(total / (1024**3), 2),
                "free_gb": round(free / (1024**3), 2),
            }
        )
    if disks:
        return disks
    try:
        usage = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        return [
            {
                "device": os.environ.get("SystemDrive", "C:") + "\\",
                "mountpoint": os.environ.get("SystemDrive", "C:") + "\\",
                "filesystem": "",
                "serial": "",
                "model": "",
                "disk_type": "",
                "total_gb": round(usage.total / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
            }
        ]
    except OSError:
        return []


def windows_network() -> list:
    data = as_list(
        parse_json(
            run_powershell(
                "Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "
                "\"IPEnabled=True\" | Select-Object Description, MACAddress, IPAddress | "
                "ConvertTo-Json -Compress"
            )
        )
    )
    rows = []
    for item in data:
        addresses = [str(value) for value in item.get("IPAddress") or []]
        rows.append(
            {
                "name": item.get("Description") or "",
                "mac": normalize_mac(item.get("MACAddress") or ""),
                "ipv4": [value for value in addresses if "." in value],
                "ipv6": [value for value in addresses if ":" in value],
            }
        )
    return rows


def windows_ports() -> list:
    rows = []
    seen = set()
    data = as_list(
        parse_json(
            run_powershell(
                "$rows = @(); "
                "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
                "ForEach-Object { $name = ''; "
                "try { $name = (Get-Process -Id $_.OwningProcess -ErrorAction Stop).ProcessName } catch { }; "
                "$rows += [pscustomobject]@{protocol='tcp'; local=\"$($_.LocalAddress)\"; "
                "port=$_.LocalPort; process=$name; state='LISTEN'} }; "
                "Get-NetUDPEndpoint -ErrorAction SilentlyContinue | "
                "ForEach-Object { $name = ''; "
                "try { $name = (Get-Process -Id $_.OwningProcess -ErrorAction Stop).ProcessName } catch { }; "
                "$rows += [pscustomobject]@{protocol='udp'; local=\"$($_.LocalAddress)\"; "
                "port=$_.LocalPort; process=$name; state='UNCONN'} }; "
                "$rows | ConvertTo-Json -Compress"
            )
        )
    )
    for row in data:
        _add_port(
            rows,
            seen,
            str(row.get("protocol") or "tcp").lower(),
            str(row.get("local") or ""),
            row.get("port"),
            str(row.get("process") or ""),
            str(row.get("state") or "LISTEN"),
        )
    if rows:
        return rows
    output = run_command(["netstat", "-ano", "-p", "tcp"])
    rows = parse_netstat_ports(output)
    udp = parse_netstat_ports(run_command(["netstat", "-ano", "-p", "udp"]))
    seen = {(row["protocol"], row["local_address"], row["port"], row["process"]) for row in rows}
    for row in udp:
        key = (row["protocol"], row["local_address"], row["port"], row["process"])
        if key not in seen and len(rows) < MAX_PORTS:
            rows.append(row)
    return rows


def windows_security() -> dict:
    data = parse_json(
        run_powershell(
            "$av = @(); "
            "try { $av = @(Get-CimInstance -Namespace root/SecurityCenter2 "
            "-ClassName AntiVirusProduct -ErrorAction Stop | "
            "Select-Object displayName, productState) } catch { }; "
            "$fw = @(); "
            "try { $fw = @(Get-NetFirewallProfile -ErrorAction Stop | "
            "Select-Object Name, Enabled) } catch { }; "
            "$bl = @(); "
            "try { $bl = @(Get-CimInstance -Namespace "
            "root/cimv2/security/microsoftvolumeencryption -ClassName "
            "Win32_EncryptableVolume -ErrorAction Stop | "
            "Select-Object DriveLetter, ProtectionStatus) } catch { }; "
            "$sb = ''; "
            "try { $sb = [string](Confirm-SecureBootUEFI -ErrorAction Stop) } catch { }; "
            "[pscustomobject]@{av=@($av); firewall=@($fw); bitlocker=@($bl); "
            "secure_boot=\"$sb\"} | ConvertTo-Json -Compress -Depth 5"
        ),
        timeout=45,
    )
    data = data if isinstance(data, dict) else {}
    antivirus = []
    edr = []
    for row in as_list(data.get("av")):
        name = str(row.get("displayName") or "").strip()
        if not name:
            continue
        try:
            state = int(row.get("productState"))
        except (TypeError, ValueError):
            state = 0
        antivirus.append(
            {
                "name": name[:200],
                "provider": "Microsoft" if "defender" in name.lower() else "",
                "enabled": bool((state >> 12) & 0x1),
                "up_to_date": bool((state >> 4) & 0x1),
            }
        )
        lowered = name.lower()
        if "defender for endpoint" in lowered:
            edr.append({"name": name[:200], "status": "running", "version": ""})
    profiles = as_list(data.get("firewall"))
    states = []
    for row in profiles:
        value = row.get("Enabled")
        if isinstance(value, bool):
            states.append(value)
        elif str(value).lower() in {"true", "1"}:
            states.append(True)
        elif str(value).lower() in {"false", "0"}:
            states.append(False)
    if states:
        firewall = {"name": "Windows Defender Firewall", "enabled": all(states)}
    else:
        output = run_command(
            ["netsh", "advfirewall", "show", "allprofiles", "state"], timeout=20
        )
        found = re.findall(r"State\s+(ON|OFF)", output, re.IGNORECASE)
        firewall = {
            "name": "Windows Defender Firewall",
            "enabled": bool(found) and all(value.upper() == "ON" for value in found),
        }
    bitlocker = as_list(data.get("bitlocker"))
    encrypted = any(str(row.get("ProtectionStatus")).strip() in {"1", "2"} for row in bitlocker)
    secure_boot_raw = str(data.get("secure_boot") or "").strip().lower()
    if secure_boot_raw in {"true", "false"}:
        secure_boot = secure_boot_raw == "true"
    else:
        secure_boot = None
    return {
        "antivirus": antivirus,
        "edr": edr,
        "firewall": firewall,
        "disk_encryption": {
            "enabled": encrypted if bitlocker else None,
            "solution": "BitLocker" if bitlocker else "",
        },
        "secure_boot": secure_boot,
    }


def windows_software(limit: int) -> list:
    import winreg

    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
    ]
    rows = []
    seen = set()
    for hive, path, flag in roots:
        try:
            root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        with root:
            index = 0
            while len(rows) < limit:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, name) as entry:
                        display_name, _ = winreg.QueryValueEx(entry, "DisplayName")
                        if not display_name:
                            continue
                        try:
                            system_component, _ = winreg.QueryValueEx(entry, "SystemComponent")
                            if int(system_component) == 1:
                                continue
                        except (OSError, TypeError, ValueError):
                            pass
                        try:
                            version, _ = winreg.QueryValueEx(entry, "DisplayVersion")
                        except OSError:
                            version = ""
                        try:
                            publisher, _ = winreg.QueryValueEx(entry, "Publisher")
                        except OSError:
                            publisher = ""
                        try:
                            installed, _ = winreg.QueryValueEx(entry, "InstallDate")
                            installed_on = (
                                f"{installed[:4]}-{installed[4:6]}-{installed[6:8]}"
                                if installed and len(str(installed)) == 8
                                else ""
                            )
                        except OSError:
                            installed_on = ""
                except OSError:
                    continue
                key = (str(display_name).casefold(), str(version or "").casefold())
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "name": str(display_name)[:255],
                        "version": str(version or "")[:120],
                        "publisher": str(publisher or "")[:200],
                        "installed_on": installed_on,
                        "source": "registry",
                    }
                )
    return rows


# ---------------------------------------------------------------------------
# Linux collectors
# ---------------------------------------------------------------------------
def read_text(path: str, limit: int = 200_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def linux_machine_id() -> str:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        value = read_text(path).strip()
        if value:
            return safe_machine_id(value)
    return safe_machine_id("")


def linux_os() -> dict:
    data = {}
    for line in read_text("/etc/os-release").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value.strip().strip('"')
    name = data.get("PRETTY_NAME") or data.get("NAME") or platform.system()
    version = data.get("VERSION_ID") or platform.release()
    boot_time = ""
    uptime_raw = read_text("/proc/uptime").split()
    if uptime_raw:
        try:
            boot = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=float(uptime_raw[0]))
            boot_time = boot.isoformat()
        except (ValueError, IndexError):
            pass
    return {
        "family": "linux",
        "name": name[:200],
        "version": version[:120],
        "build": platform.release()[:120],
        "architecture": platform.machine(),
        "boot_time": boot_time,
    }


def linux_logged_in_user() -> str:
    users = []
    for line in run_command(["who"], timeout=10).splitlines():
        parts = line.split()
        if parts:
            users.append(parts[0][:150])
    for user in users:
        if user != "root":
            return user
    if users:
        return users[0]
    return (
        os.environ.get("SUDO_USER")
        or os.environ.get("LOGNAME")
        or os.environ.get("USER", "")
    )[:150]


def linux_system() -> dict:
    def dmi(name: str) -> str:
        return read_text(f"/sys/class/dmi/id/{name}").strip()

    return {
        "manufacturer": dmi("sys_vendor"),
        "model": dmi("product_name"),
        "serial_number": _clean_serial(dmi("product_serial")),
        "uuid": _clean_serial(dmi("product_uuid")),
        "bios_version": dmi("bios_version")[:120],
        "bios_serial": _clean_serial(dmi("board_serial") or dmi("chassis_serial")),
        "domain": socket.getfqdn().split(".", 1)[1] if "." in socket.getfqdn() else "",
        "logged_in_user": linux_logged_in_user(),
    }


def linux_cpu() -> dict:
    model = ""
    physical_ids = set()
    cores = set()
    logical = 0
    for line in read_text("/proc/cpuinfo").splitlines():
        if ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        if key == "model name" and not model:
            model = value
        elif key == "physical id":
            physical_ids.add(value)
        elif key == "core id":
            cores.add(value)
        elif key == "processor":
            logical += 1
    physical = len(physical_ids) * len(cores) if physical_ids and cores else None
    return {
        "model": model[:200],
        "architecture": platform.machine(),
        "physical_cores": physical,
        "logical_cores": logical or os.cpu_count(),
        "max_clock_mhz": None,
    }


def linux_memory() -> dict:
    values = {}
    for line in read_text("/proc/meminfo").splitlines():
        if ":" in line:
            key, raw = line.split(":", 1)
            values[key] = raw.strip()
    def kb(key: str) -> int | None:
        try:
            return int(values.get(key, "0").split()[0])
        except (IndexError, ValueError):
            return None

    total = kb("MemTotal")
    available = kb("MemAvailable")
    return {
        "total_mb": int(total / 1024) if total else None,
        "available_mb": int(available / 1024) if available else None,
    }


def linux_disks() -> list:
    data = parse_json(
        run_command(
            [
                "lsblk",
                "-J",
                "-b",
                "-o",
                "NAME,MOUNTPOINT,FSTYPE,SIZE,TYPE,ROTA,TRAN,MODEL,SERIAL",
            ]
        )
    )
    rows = []

    def device_type(item: dict, parent: dict | None) -> str:
        source = item if item.get("type") == "disk" else (parent or item)
        transport = str(source.get("tran") or "").lower()
        if transport == "nvme":
            return "nvme"
        rota = source.get("rota")
        if rota in (False, 0, "0", "false"):
            return "ssd"
        if rota in (True, 1, "1", "true"):
            return "hdd"
        return transport

    def walk(devices, parent=None):
        for item in devices or []:
            current = item if item.get("type") == "disk" else parent
            mount = item.get("mountpoint")
            if mount:
                try:
                    total = int(item.get("size") or 0)
                    usage = shutil.disk_usage(mount)
                    source = current or item
                    rows.append(
                        {
                            "device": item.get("name") or "",
                            "mountpoint": mount,
                            "filesystem": item.get("fstype") or "",
                            "serial": _clean_serial(source.get("serial")),
                            "model": str(source.get("model") or "").strip()[:120],
                            "disk_type": device_type(item, parent),
                            "total_gb": round((total or usage.total) / (1024**3), 2),
                            "free_gb": round(usage.free / (1024**3), 2),
                        }
                    )
                except (OSError, TypeError, ValueError):
                    pass
            walk(item.get("children") or [], current)

    if isinstance(data, dict):
        walk(data.get("blockdevices"))
    if rows:
        return rows
    output = run_command(["df", "-kP"])
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6 or not parts[5].startswith("/"):
            continue
        try:
            total = int(parts[1]) * 1024
            free = int(parts[3]) * 1024
        except ValueError:
            continue
        rows.append(
            {
                "device": parts[0],
                "mountpoint": parts[5],
                "filesystem": parts[0],
                "serial": "",
                "model": "",
                "disk_type": "",
                "total_gb": round(total / (1024**3), 2),
                "free_gb": round(free / (1024**3), 2),
            }
        )
    return rows


def linux_network() -> list:
    data = parse_json(run_command(["ip", "-j", "addr", "show"]))
    rows = []
    if isinstance(data, list):
        for item in data:
            addresses = item.get("addr_info") or []
            ipv4 = [entry.get("local") for entry in addresses if entry.get("family") == "inet"]
            ipv6 = [entry.get("local") for entry in addresses if entry.get("family") == "inet6"]
            rows.append(
                {
                    "name": item.get("ifname") or "",
                    "mac": item.get("address") or "",
                    "ipv4": [value for value in ipv4 if value],
                    "ipv6": [value for value in ipv6 if value],
                }
            )
    if rows:
        return rows
    try:
        host = socket.gethostname()
        addresses = socket.getaddrinfo(host, None)
        ipv4 = sorted({item[4][0] for item in addresses if ":" not in item[4][0]})
        ipv6 = sorted({item[4][0] for item in addresses if ":" in item[4][0]})
        return [{"name": host, "mac": "", "ipv4": ipv4, "ipv6": ipv6}]
    except OSError:
        return []


def linux_software(limit: int) -> list:
    rows = []
    seen = set()
    if shutil.which("dpkg-query"):
        output = run_command(
            [
                "dpkg-query",
                "-W",
                "-f",
                "${Package}\t${Version}\t${Maintainer}\n",
            ],
            timeout=60,
        )
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name, version = parts[0], parts[1]
            key = (name.casefold(), version.casefold())
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "name": name[:255],
                    "version": version[:120],
                    "publisher": (parts[2] if len(parts) > 2 else "")[:200],
                    "installed_on": "",
                    "source": "dpkg",
                }
            )
            if len(rows) >= limit:
                return rows
    if shutil.which("rpm"):
        output = run_command(
            [
                "rpm",
                "-qa",
                "--qf",
                "%{NAME}\t%{VERSION}-%{RELEASE}\t%{VENDOR}\n",
            ],
            timeout=60,
        )
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            key = (parts[0].casefold(), parts[1].casefold())
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "name": parts[0][:255],
                    "version": parts[1][:120],
                    "publisher": (parts[2] if len(parts) > 2 else "")[:200],
                    "installed_on": "",
                    "source": "rpm",
                }
            )
            if len(rows) >= limit:
                return rows
    if not rows and shutil.which("pacman"):
        output = run_command(["pacman", "-Q"], timeout=60)
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            rows.append(
                {
                    "name": parts[0][:255],
                    "version": parts[1][:120],
                    "publisher": "",
                    "installed_on": "",
                    "source": "pacman",
                }
            )
            if len(rows) >= limit:
                break
    return rows


# ---------------------------------------------------------------------------
# macOS collectors
# ---------------------------------------------------------------------------
def macos_machine_id() -> str:
    output = run_command(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', output)
    return safe_machine_id(match.group(1) if match else "")


def macos_os() -> dict:
    version = platform.mac_ver()[0] or platform.release()
    return {
        "family": "macos",
        "name": f"macOS {version}",
        "version": version,
        "architecture": platform.machine(),
        "boot_time": "",
    }


def macos_system() -> dict:
    model = run_command(["sysctl", "-n", "hw.model"])
    output = run_command(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    serial = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', output)
    return {
        "manufacturer": "Apple Inc.",
        "model": model,
        "serial_number": serial.group(1) if serial else "",
        "uuid": "",
        "domain": "",
        "logged_in_user": getpass.getuser(),
    }


def macos_cpu() -> dict:
    model = run_command(["sysctl", "-n", "machdep.cpu.brand_string"])
    return {
        "model": model,
        "physical_cores": None,
        "logical_cores": os.cpu_count(),
        "max_clock_mhz": None,
    }


def macos_memory() -> dict:
    raw = run_command(["sysctl", "-n", "hw.memsize"])
    try:
        total = int(raw) / (1024 * 1024)
    except (TypeError, ValueError):
        total = None
    return {"total_mb": int(total) if total else None, "available_mb": None}


def macos_disks() -> list:
    try:
        usage = shutil.disk_usage("/")
        return [
            {
                "device": "/dev/disk1",
                "mountpoint": "/",
                "filesystem": "APFS",
                "total_gb": round(usage.total / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
            }
        ]
    except OSError:
        return []


def macos_network() -> list:
    try:
        host = socket.gethostname()
        addresses = socket.getaddrinfo(host, None)
        ipv4 = sorted({item[4][0] for item in addresses if ":" not in item[4][0]})
        ipv6 = sorted({item[4][0] for item in addresses if ":" in item[4][0]})
        return [{"name": "primary", "mac": "", "ipv4": ipv4, "ipv6": ipv6}]
    except OSError:
        return []


def macos_software(limit: int) -> list:
    import plistlib

    rows = []
    for folder in ("/Applications", "/Applications/Utilities"):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".app") or len(rows) >= limit:
                continue
            info_path = os.path.join(folder, name, "Contents", "Info.plist")
            version = ""
            identifier = ""
            try:
                with open(info_path, "rb") as handle:
                    info = plistlib.load(handle)
                version = str(
                    info.get("CFBundleShortVersionString")
                    or info.get("CFBundleVersion")
                    or ""
                )
                identifier = str(info.get("CFBundleIdentifier") or "")
            except (OSError, ValueError, plistlib.InvalidFileException):
                pass
            rows.append(
                {
                    "name": name[:-4][:255],
                    "version": version[:120],
                    "publisher": identifier[:200],
                    "installed_on": "",
                    "source": "applications",
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Inventory assembly
# ---------------------------------------------------------------------------
def collect_inventory(software_limit: int = DEFAULT_SOFTWARE_LIMIT) -> dict:
    software_limit = max(0, min(int(software_limit), 5000))
    if WINDOWS:
        machine_id = windows_machine_id()
        os_info = windows_os()
        system = windows_system()
        cpu = windows_cpu()
        memory = windows_memory()
        disks = windows_disks()
        network = windows_network()
        software = windows_software(software_limit) if software_limit else []
    elif LINUX:
        machine_id = linux_machine_id()
        os_info = linux_os()
        system = linux_system()
        cpu = linux_cpu()
        memory = linux_memory()
        disks = linux_disks()
        network = linux_network()
        software = linux_software(software_limit) if software_limit else []
    elif MACOS:
        machine_id = macos_machine_id()
        os_info = macos_os()
        system = macos_system()
        cpu = macos_cpu()
        memory = macos_memory()
        disks = macos_disks()
        network = macos_network()
        software = macos_software(software_limit) if software_limit else []
    else:
        machine_id = safe_machine_id("")
        os_info = {
            "family": "other",
            "name": platform.platform(),
            "version": platform.release(),
            "architecture": platform.machine(),
            "boot_time": "",
        }
        system = {
            "manufacturer": "",
            "model": "",
            "serial_number": "",
            "uuid": "",
            "domain": "",
            "logged_in_user": getpass.getuser(),
        }
        cpu = {
            "model": platform.processor(),
            "physical_cores": None,
            "logical_cores": os.cpu_count(),
            "max_clock_mhz": None,
        }
        memory = {"total_mb": None, "available_mb": None}
        disks = []
        network = []
        software = []

    return {
        "collected_at": iso_now(),
        "machine_id": machine_id,
        "hostname": clean_hostname(),
        "os": os_info,
        "system": system,
        "cpu": cpu,
        "memory": memory,
        "disks": disks,
        "network": network,
        "software": software,
        "agent": {"version": AGENT_VERSION, "platform": sys.platform},
    }


# ---------------------------------------------------------------------------
# HTTP and state
# ---------------------------------------------------------------------------
def http_json(
    url: str,
    payload: dict,
    *,
    headers: dict | None = None,
    insecure: bool = False,
    timeout: int = 30,
) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers=request_headers,
    )
    context = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            body = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        details = parse_json(body) or {}
        raise AgentHTTPError(
            details.get("message") or f"Server returned HTTP {exc.code}.",
            status=exc.code,
            code=details.get("error") or "",
        ) from exc
    except urllib.error.URLError as exc:
        raise AgentHTTPError(f"Cannot reach {url}: {exc.reason}") from exc
    except OSError as exc:
        raise AgentHTTPError(f"Network error: {exc}") from exc
    parsed = parse_json(body)
    if status >= 400 or not isinstance(parsed, dict):
        raise AgentHTTPError(
            f"Unexpected server response (HTTP {status}).", status=status
        )
    if not parsed.get("ok"):
        raise AgentHTTPError(
            parsed.get("message") or "The server rejected the report.",
            status=status,
            code=parsed.get("error") or "",
        )
    return parsed


def default_state_path() -> str:
    home = os.path.expanduser("~")
    if WINDOWS:
        program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return os.path.join(program_data, "SORA", "agent.json")
    if MACOS:
        return "/Library/Application Support/SORA/agent.json"
    return "/etc/sora-agent/agent.json"


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    if not WINDOWS:
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def persist_state(path: str, state: dict) -> str:
    try:
        save_state(path, state)
        return path
    except PermissionError:
        fallback = os.path.join(os.path.expanduser("~"), ".sora-agent", "agent.json")
        warn(f"Cannot write {path}; using {fallback} instead.")
        save_state(fallback, state)
        return fallback


def normalize_server(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not re.match(r"^https?://", value):
        warn("Server URL should start with http:// or https://; assuming https://.")
        value = "https://" + value
    if value.startswith("http://") and "localhost" not in value and "127.0.0.1" not in value:
        warn("Plain HTTP is not encrypted. Use HTTPS in production.")
    return value


def enroll(server: str, token: str, *, insecure: bool, timeout: int) -> tuple[dict, str]:
    payload = collect_inventory(0)
    body = {
        "enrollment_token": token,
        "machine_id": payload["machine_id"],
        "hostname": payload["hostname"],
        "os_family": payload["os"]["family"],
        "os_version": payload["os"]["version"],
        "architecture": payload["os"]["architecture"],
        "agent_version": AGENT_VERSION,
    }
    response = http_json(
        f"{server}/api/assets/agent/enroll",
        body,
        insecure=insecure,
        timeout=timeout,
    )
    if not response.get("agent_id") or not response.get("agent_key"):
        raise AgentHTTPError("The server did not return agent credentials.")
    log(
        f"Enrolled {payload['hostname']} with "
        f"{response.get('workspace', {}).get('name') or 'the workspace'}."
    )
    return response, payload["machine_id"]


def send_inventory(server: str, state: dict, *, insecure: bool, timeout: int, software_limit: int) -> dict:
    payload = collect_inventory(software_limit)
    payload["machine_id"] = state["machine_id"]
    response = http_json(
        f"{server}/api/assets/agent/inventory",
        payload,
        headers=headers_auth(state),
        insecure=insecure,
        timeout=timeout,
    )
    log(
        f"Reported inventory: {response.get('software_count', len(payload['software']))} "
        f"application(s), {len(payload['disks'])} volume(s)."
    )
    return response


def headers_auth(state: dict) -> dict:
    return {"Authorization": f"Bearer {state['agent_id']}:{state['agent_key']}"}


def run(args) -> int:
    if args.print_payload:
        print(json.dumps(collect_inventory(args.software_limit), indent=2, default=str))
        return 0

    state_path = os.path.expanduser(args.state or default_state_path())
    state = load_state(state_path)
    server = normalize_server(args.server or state.get("server") or "")
    token = (args.enroll_token or "").strip()

    if not server:
        warn("A server URL is required on first run: --server https://erp.example.com")
        return 2

    if token or not state.get("agent_key"):
        if not token:
            warn("No agent credentials found. Pass --enroll-token on the first run.")
            return 2
        response, machine_id = enroll(
            server, token, insecure=args.insecure, timeout=args.timeout
        )
        state.update(
            {
                "server": server,
                "agent_id": response["agent_id"],
                "agent_key": response["agent_key"],
                "machine_id": machine_id,
                "enrolled_at": iso_now(),
            }
        )
        state_path = persist_state(state_path, state)
        log(f"Credentials stored in {state_path}.")
    else:
        state["server"] = server
        state_path = persist_state(state_path, state)

    try:
        send_inventory(
            server,
            state,
            insecure=args.insecure,
            timeout=args.timeout,
            software_limit=args.software_limit,
        )
    except AgentHTTPError as exc:
        warn(str(exc))
        if exc.status in {401, 403}:
            return 3
        if args.once:
            return 1
    if args.once:
        return 0

    interval = max(int(args.interval or state.get("interval") or DEFAULT_INTERVAL), MIN_INTERVAL)
    log(f"Reporting every {interval} seconds. Press Ctrl+C to stop.")
    while True:
        time.sleep(interval)
        try:
            send_inventory(
                server,
                state,
                insecure=args.insecure,
                timeout=args.timeout,
                software_limit=args.software_limit,
            )
        except AgentHTTPError as exc:
            warn(str(exc))
            if exc.status in {401, 403}:
                warn("Agent credentials were rejected. Re-enrol this endpoint with a new token.")
                return 3
        except KeyboardInterrupt:
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SORA ERP endpoint inventory agent (Windows, Linux, macOS)."
    )
    parser.add_argument("--server", help="ERP base URL, e.g. https://erp.example.com")
    parser.add_argument(
        "--enroll-token",
        help="One-time enrolment token from Assets › Endpoint Agents › Enrolment Tokens",
    )
    parser.add_argument("--state", help="Path to the credential state file")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Report interval in seconds (default 300)")
    parser.add_argument("--once", action="store_true", help="Send one report and exit")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification (testing only)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument(
        "--software-limit",
        type=int,
        default=DEFAULT_SOFTWARE_LIMIT,
        help="Maximum number of installed applications to report",
    )
    parser.add_argument(
        "--print-payload",
        action="store_true",
        help="Print the collected inventory JSON without contacting the server",
    )
    parser.add_argument("--version", action="version", version=f"SORA inventory agent {AGENT_VERSION}")
    return parser


def main(argv=None) -> int:
    try:
        if WINDOWS:
            try:
                ctypes.windll.kernel32.SetConsoleTitleW("SORA Inventory Agent")
            except Exception:
                pass
        args = build_parser().parse_args(argv)
        return run(args)
    except KeyboardInterrupt:
        return 0
    except AgentHTTPError as exc:
        warn(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
