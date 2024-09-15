#!/usr/bin/env python3
"""
Script to install or update an RPM package from a link
"""

import argparse
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from packaging.version import Version
from version_utils import rpm

endpoints = ["url", "json", "github"]

presets = {
    "plex": {
        "endpoint": "json",
        "json_url": "https://plex.tv/api/downloads/5.json",
        "json_selector": "['computer']['Linux']['releases'][? distro == 'redhat' & build = 'linux-x86_64']['url']",
    },
    "lapce": {
        "endpoint": "github",
        "repo": "lapce/lapce",
        "file_selector": ".rpm",
    },
    "thorium": {
        "endpoint": "github",
        "repo": "Alex313031/thorium",
        "file_selector": "AVX2.rpm",
    },
    "azuredatastudio": {
        "endpoint": "url",
        "url": "https://azuredatastudio-update.azurewebsites.net/latest/linux-rpm-x64/stable",
    },
    # UNTESTED
    "azure-cli": {
        "endpoint": "url",
        "url": "https://aka.ms/InstallAzureCliRpmEl8Edge",
    },
}

# TODO endpoint type can probably be auto detected


parser = argparse.ArgumentParser(
    prog="update-rpm",
    description="Fetch the latest release RPM of a package from a download link. Install it.",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
subparsers = parser.add_subparsers(
    help="The type of endpoint you want to install from. Or choose from a given preset.",
    dest="endpoint",
)

url_parser_help = "RPM direct download URL. The url can be a redirect."
url_parser = subparsers.add_parser("url", help=url_parser_help)
url_parser.add_argument("url", help=url_parser_help, type=str)

github_parser_help = "Download RPM from Github releases"
github_parser = subparsers.add_parser("github", help=github_parser_help)
github_parser.add_argument(
    "repo",
    help="{owner}/{repository} Owner and repository name. Example: 'lapce/lapce'",
    type=str,
)
github_parser.add_argument(
    "-s",
    "--file_selector",
    help="File Selector. The first file name that contains the given string will be chosen. Example: 'x86_64.rpm'",
    default=".rpm",
    type=str,
)

json_parser_help = "URL to JSON that contains download URL for the RPM file"
json_parser = subparsers.add_parser("json", help=json_parser_help)
json_parser.add_argument("json_url", help=json_parser_help, type=str)
json_parser.add_argument(
    "-s",
    "--json_selector",
    help="JSON Selector. For syntax see https://github.com/h2non/jsonpath-ng?tab=readme-ov-file#",
    type=str,
)

preset_parser = subparsers.add_parser("preset", help="Choose from a given preset")
preset_parser.add_argument(
    "preset", help=str(presets), choices=presets.keys(), type=str
)

parser.add_argument(
    "-d",
    "--directory",
    help="Directory to store the RPM in",
    default=tempfile.gettempdir(),
)
parser.add_argument(
    "-r",
    "--redownload",
    help="Force download the RPM if it is already downloaded",
    default=False,
    action="store_true",
)
parser.add_argument(
    "-i",
    "--reinstall",
    help="Whether to reinstall if the same package version is already installed",
    default=False,
    action="store_true",
)

args = parser.parse_args()
endpoint = args.endpoint
redownload = args.redownload
reinstall = args.reinstall
directory = args.directory

if endpoint == "preset":
    args = parser.parse_args(presets[args.preset])
    endpoint = args.endpoint

if endpoint == "github":
    try:
        owner, repo = args.repo.split("/")[-2:]
    except Exception as e:
        print(e)
        print("Invalid owner + repo: ", args.repo)
        sys.exit(1)
    json_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    file_selector = args.file_selector

    resp = requests.get(json_url)
    js = resp.json()
    version = js["tag_name"][1:]
    try:
        release = next(
            x for x in js["assets"] if file_selector.lower() in x["name"].lower()
        )
    except StopIteration:
        names = "\n".join([x["name"] for x in js["assets"]])
        print(f"{file_selector=} not found. Available options:\n{names}")
        sys.exit(1)
    url = release["browser_download_url"]
    fname = release["name"]
elif endpoint == "json":
    json_url = args.json_url

if endpoint in ("json", "github"):
    pass

# Get file name after redirects. We assume that the package version is included in the file name.
resp = requests.head(args.url, allow_redirects=True)
url = resp.url
fname = url.split("/")[-1]
path = Path(directory / fname)

# Check if file exists to avoid redownload
if path.exists():
    if redownload:
        download = True
    else:
        download = False
else:
    download = True


# Attempt to check if package is already installed, and if so, its version.
# We must infer package name and version from file name which might not always work; if e.g. the package is named "xx.rpm".
# If the package is already installed, we might break out of the program early to avoid downloading the file again.
try:
    inferred_package_name = rpm.package(fname).name
    # TODO get real package name and version by downloading only first 1kB
    # $ curl -s -L -r 0-1024 -o /tmp/foo.rpm https://azuredatastudio-update.azurewebsites.net/latest/linux-rpm-x64/stable; file /tmp/foo.rpm
    # /tmp/foo.rpm: RPM v3.0 bin i386/x86_64 azuredatastudio-1.49.1-1723572669.el7
except IndexError:
    # Could not infer package name
    pass
else:
    # Find the installed RPM package name and version
    installed_versions = subprocess.run(
        "rpm -q " + str(inferred_package_name),
        shell=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if len(installed_versions) == 1:
        installed_version = installed_versions[0]
        if not installed_version.endswith(
            "not installed"
        ) and not installed_version.endswith("NO KEY"):
            version_cmp = rpm.compare_packages(fname, installed_version)
            if version_cmp < 0:
                print(
                    f"Detected that {installed_version} is installed, but {fname} is newer"
                )
            elif version_cmp == 0:
                print(
                    f"Detected that {installed_version} is installed, but {fname} is same"
                )
                if not reinstall:
                    sys.exit(0)
            else:
                print(
                    f"Detected that {installed_version} is installed, but {fname} is older"
                )
                sys.exit(0)


if download:
    print(f"Downloading {url} to {path}")
    resp = requests.get(url)
    with open(path, "wb") as fil:
        fil.write(resp.content)


# Find the RPM package name
package_name = subprocess.run(
    'rpm -qp --queryformat "%{NAME}" ' + str(path),
    shell=True,
    capture_output=True,
    text=True,
).stdout

# Check if package is already installed, and find the version number of it
cmd_result = subprocess.run(
    "rpm --queryformat '%{VERSION}\n' -q " + package_name,
    shell=True,
    capture_output=True,
    text=True,
)
installed_versions = cmd_result.stdout.splitlines()

install = True
if len(installed_versions) == 1:
    installed_version = installed_versions[0]
    if not installed_version.endswith("not installed"):
        print(f"{package_name} {installed_version} already installed")
        rpm_version = subprocess.run(
            'rpm -qp --queryformat "%{VERSION}" ' + str(path),
            shell=True,
            capture_output=True,
            text=True,
        ).stdout
        if Version(installed_version) < Version(rpm_version):
            install = True
        elif Version(installed_version) == Version(rpm_version):
            if reinstall:
                install = True
            else:
                print(f"Version {installed_version} already installed; not installing.")
                install = False
        else:
            print(
                f"Installed version {installed_version} newer than downloaded version {rpm_version}; not installing."
            )

if install:
    cmd = ["sudo", "-S", "dnf", "install", "--assumeyes", str(path)]
    cmd_s = shlex.join(cmd)
    print("Installing ...")
    print(cmd_s)
    subprocess.call(cmd)
