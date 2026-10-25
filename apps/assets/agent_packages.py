"""Deployment packages for the SORA endpoint discovery agent.

Every package embeds a single-use tenant bootstrap token, so downloads must be
served over HTTPS with ``Cache-Control: no-store`` and the token rotated once
the rollout is complete.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from django.conf import settings

from apps.assets import agent_services

AGENT_SOURCE = Path(settings.BASE_DIR) / "agent" / "sora_asset_agent.py"
PLATFORMS = {"windows", "linux"}


class PackageError(Exception):
    pass


def agent_source() -> str:
    if not AGENT_SOURCE.exists():
        raise PackageError("The endpoint agent source is not installed on this server.")
    return AGENT_SOURCE.read_text(encoding="utf-8")


def agent_version() -> str:
    match = re.search(r'^AGENT_VERSION\s*=\s*"([^"]+)"', agent_source(), re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def package_config(
    *,
    server_url: str,
    raw_token: str,
    label: str = "",
    provisioning_method: str = "manual",
    organization: str = "",
) -> dict:
    policy = agent_services.agent_policy()
    return {
        "server_url": server_url.rstrip("/"),
        "enrollment_token": raw_token,
        "label": label,
        "organization": organization,
        "provisioning_method": provisioning_method,
        "heartbeat_seconds": policy["heartbeat_seconds"],
        "inventory_minutes": policy["inventory_minutes"],
        "agent_version": agent_version(),
    }


LINUX_INSTALL = r"""#!/bin/sh
# SORA ERP Asset Discovery Agent - Linux / systemd installer
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root (sudo)." >&2
    exit 1
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${SORA_AGENT_PREFIX:-/opt/sora-agent}"
CONFIG="/etc/sora-agent.json"
UNIT="/etc/systemd/system/sora-asset-agent.service"
PYTHON="${SORA_PYTHON:-}"

if [ -z "$PYTHON" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON=$(command -v "$candidate")
            break
        fi
    done
fi

if [ -z "$PYTHON" ]; then
    echo "python3 is required; install it with your package manager." >&2
    exit 1
fi

"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 8):
    raise SystemExit("Python 3.8 or newer is required.")
PY

install -d -m 755 "$PREFIX"
install -m 755 "$HERE/sora_asset_agent.py" "$PREFIX/sora_asset_agent.py"
install -m 600 "$HERE/sora-agent.json" "$CONFIG"

cat > "$UNIT" <<EOF
[Unit]
Description=SORA ERP Asset Discovery Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$PYTHON $PREFIX/sora_asset_agent.py --config $CONFIG --daemon
Restart=always
RestartSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/var/lib

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable sora-asset-agent.service
systemctl restart sora-asset-agent.service
echo "SORA asset agent installed and started."
"""

WINDOWS_INSTALL = r"""# SORA ERP Asset Discovery Agent - Windows installer (GPO / MSI / manual)
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramData\SORA\Agent",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-Python {
    if ($Python -and (Test-Path $Python)) { return $Python }
    foreach ($name in @("py", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            if ($name -eq "py") {
                $resolved = & $cmd.Source -3 -c "import sys; print(sys.executable)" 2>$null
            } else {
                $resolved = & $cmd.Source -c "import sys; print(sys.executable)" 2>$null
            }
            if ($LASTEXITCODE -eq 0 -and $resolved) { return $resolved.Trim() }
        }
    }
    return $null
}

$pythonPath = Resolve-Python
if (-not $pythonPath) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Installing Python silently with winget..."
        & $winget.Source install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements | Out-Null
        $pythonPath = Resolve-Python
    }
}
if (-not $pythonPath) {
    throw "Python 3.8+ is required. Install Python or pass -Python <path to python.exe>."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item (Join-Path $here "sora_asset_agent.py") $InstallDir -Force
Copy-Item (Join-Path $here "sora-agent.json") $InstallDir -Force

& icacls $InstallDir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null

$taskName = "SORA Asset Discovery Agent"
$arguments = '"{0}" --config "{1}" --daemon' -f (Join-Path $InstallDir "sora_asset_agent.py"), (Join-Path $InstallDir "sora-agent.json")
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "SORA asset agent installed and started as scheduled task '$taskName'."
"""

BUILD_MSI = r"""# Build a silent MSI for GPO / Intune deployment.
# Requires the WiX Toolset v4+ (dotnet tool install --global wix).
param(
    [string]$Version = "1.0.0",
    [string]$Output = "sora-asset-agent.msi"
)
$ErrorActionPreference = "Stop"
$wix = Get-Command wix -ErrorAction SilentlyContinue
if (-not $wix) { throw "WiX v4 is required: dotnet tool install --global wix --add-source https://api.nuget.org/v3/index.json" }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& $wix.Source build (Join-Path $here "sora-agent.wxs") -d Version=$Version -o (Join-Path $here $Output)
Write-Host "Built $Output - deploy with: msiexec /i $Output /qn"
"""

WXS_TEMPLATE = r"""<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs"
     xmlns:util="http://wixtoolset.org/schemas/v4/wxs/util">
  <Package Name="SORA Asset Discovery Agent" Manufacturer="SORA ERP"
           Version="$(var.Version)" UpgradeCode="7B1D2E4A-5C6F-4B8E-9C21-8F3A6D5E4C11">
    <MajorUpgrade DowngradeErrorMessage="A newer SORA agent is already installed." />
    <MediaTemplate EmbedCab="yes" />

    <StandardDirectory Id="ProgramFiles64Folder">
      <Directory Id="INSTALLFOLDER" Name="SORA" />
    </StandardDirectory>

    <Feature Id="Agent" Title="SORA Asset Discovery Agent" Level="1">
      <ComponentGroupRef Id="AgentFiles" />
    </Feature>

    <ComponentGroup Id="AgentFiles" Directory="INSTALLFOLDER">
      <Files Include="sora_asset_agent.py" />
      <Files Include="sora-agent.json" />
      <Files Include="install.ps1" />
    </ComponentGroup>

    <SetProperty Id="RunAgentInstaller"
                 Value="powershell.exe -NoProfile -ExecutionPolicy Bypass -File &quot;[INSTALLFOLDER]install.ps1&quot; -InstallDir &quot;[INSTALLFOLDER]&quot;"
                 Before="RunAgentInstaller" Sequence="execute" />
    <CustomAction Id="RunAgentInstaller" BinaryRef="Wix4UtilCA_X64"
                  DllEntry="WixQuietExec" Execute="deferred" Impersonate="no" Return="check" />

    <InstallExecuteSequence>
      <Custom Action="RunAgentInstaller" After="InstallFiles" Condition="NOT Installed" />
    </InstallExecuteSequence>
  </Package>
</Wix>
"""


def readme_text(config: dict, platform: str) -> str:
    token_prefix = (config.get("enrollment_token") or "")[:20]
    if platform == "linux":
        steps = (
            "1. Copy this folder to the target host.\n"
            "2. sudo sh install.sh\n"
            "3. systemctl status sora-asset-agent\n"
        )
        return (
            "SORA ERP Asset Discovery Agent\n"
            "==============================\n\n"
            f"Workspace: {config.get('organization') or config.get('server_url')}\n"
            f"Token:     {token_prefix}... (single use)\n\n"
            "This bundle contains a one-time bootstrap token. Keep it on a\n"
            "protected share, deploy promptly and revoke the token afterwards.\n\n"
            "Linux (systemd)\n"
            "---------------\n" + steps + "\n"
            "The agent registers once, receives a hardware-bound access token and\n"
            "refreshes it automatically. It reports a heartbeat and a full\n"
            "inventory on the schedule defined in sora-agent.json.\n"
        )
    return (
        "SORA ERP Asset Discovery Agent\n"
        "==============================\n\n"
        f"Workspace: {config.get('organization') or config.get('server_url')}\n"
        f"Token:     {token_prefix}... (single use)\n\n"
        "This bundle contains a one-time bootstrap token. Keep it on a\n"
        "protected share, deploy promptly and revoke the token afterwards.\n\n"
        "Active Directory GPO (startup script)\n"
        "-------------------------------------\n"
        "1. Extract the bundle into a SYSVOL share readable by Domain Computers.\n"
        "2. Add install.ps1 as a Computer Configuration startup script.\n"
        "3. The script installs Python when missing (winget), protects the\n"
        "   configuration with ACLs and registers a SYSTEM scheduled task.\n\n"
        "Silent MSI / Intune\n"
        "-------------------\n"
        "1. On a build host install WiX v4: dotnet tool install --global wix\n"
        "2. Run: powershell -File build_msi.ps1\n"
        "3. Deploy with: msiexec /i sora-asset-agent.msi /qn\n\n"
        "Manual install\n"
        "--------------\n"
        "powershell -ExecutionPolicy Bypass -File install.ps1\n"
    )


def build_package_zip(config: dict, platform: str) -> bytes:
    if platform not in PLATFORMS:
        raise PackageError("Unsupported platform.")
    payload = {
        "sora_asset_agent.py": agent_source(),
        "sora-agent.json": json.dumps(config, indent=2),
        "README.txt": readme_text(config, platform),
    }
    if platform == "linux":
        payload["install.sh"] = LINUX_INSTALL
    else:
        payload["install.ps1"] = WINDOWS_INSTALL
        payload["build_msi.ps1"] = BUILD_MSI
        payload["sora-agent.wxs"] = WXS_TEMPLATE.replace("$(var.Version)", config.get("agent_version", "1.0.0"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def linux_bootstrap_script(*, server_url: str, raw_token: str, platform: str = "linux") -> str:
    base = server_url.rstrip("/")
    token = raw_token.replace("'", "")
    return (
        "#!/bin/sh\n"
        "# SORA ERP endpoint onboarding - one-line Linux bootstrap\n"
        "set -eu\n"
        'if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo." >&2; exit 1; fi\n'
        'command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }\n'
        "TMP=$(mktemp -d)\n"
        'trap \'rm -rf "$TMP"\' EXIT\n'
        f"SERVER='{base}'\n"
        f"TOKEN='{token}'\n"
        'curl -fsSL "$SERVER/api/assets/agent/package?platform=linux&token=$TOKEN" -o "$TMP/agent.zip"\n'
        'python3 -m zipfile -e "$TMP/agent.zip" "$TMP/pkg" 2>/dev/null || unzip -q "$TMP/agent.zip" -d "$TMP/pkg"\n'
        'sh "$TMP/pkg/install.sh"\n'
    )
