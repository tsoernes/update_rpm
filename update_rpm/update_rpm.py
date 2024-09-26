#!/usr/bin/env python3
"""
Script to install or update an RPM package from a link
"""

import argparse
import pprint
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from jsonpath_ng.ext import parse
from packaging.version import Version
from version_utils import rpm

endpoints = ["url", "json", "github", "html"]

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
    "microsoft-repo": {
        "endpoint": "url",
        "url": "https://packages.microsoft.com/config/fedora/40/packages-microsoft-prod.rpm",
    },
    "azuredatastudio": {
        "endpoint": "url",
        "url": "https://azuredatastudio-update.azurewebsites.net/latest/linux-rpm-x64/stable",
    },
    "azure-cli": {
        "endpoint": "url",
        "url": "https://aka.ms/InstallAzureCliRpmEl8Edge",
    },
    "chrome": {
        "endpoint": "url",
        "url": "https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm",
    },
    "vscode": {
        "endpoint": "url",
        "url": "https://update.code.visualstudio.com/latest/linux-rpm-x64/stable",
    },
    "docker": {
        "endpoint": "html",
        "url": "https://download.docker.com/linux/fedora/41/x86_64/stable/Packages/",
        "regex_selector": r"docker-ce.*x86_64\.rpm$",
    },
    "slack": {
        "endpoint": "html",
        "url": "https://slack.com/downloads/instructions/linux?ddl=1&build=rpm",
        "regex_selector": r"slack.*\.rpm$",
    },
    "skype": {
        "endpoint": "url",
        "url": "https://repo.skype.com/latest/skypeforlinux-64.rpm",
    },
    "teamviewer": {
        "endpoint": "url",
        "url": "https://download.teamviewer.com/download/linux/teamviewer.x86_64.rpm",
    },
    "google-earth": {
        "endpoint": "url",
        "url": "https://dl.google.com/dl/earth/client/current/google-earth-pro-stable_current_x86_64.rpm",
    },
}


def parse_args():
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

    html_parser_help = "URL to HTML page that contains download links for the RPM file"
    html_parser = subparsers.add_parser("html", help=html_parser_help)
    html_parser.add_argument("url", help=html_parser_help, type=str)
    html_parser.add_argument(
        "-r",
        "--regex_selector",
        help=r"Regex Selector. The first link that searches the given regex will be chosen. Example: '^docker-ce.*x86_64\.rpm$'",
        type=str,
        default=r"\.rpm$",
    )

    preset_parser = subparsers.add_parser("preset", help="Choose from a given preset")
    preset_parser.add_argument(
        "preset",
        help="\n" + str(pprint.pformat(presets)),
        choices=presets.keys(),
        type=str,
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

    return parser.parse_args()


def get_github_release(repo, file_selector):
    try:
        owner, repo_name = repo.split("/")
    except ValueError:
        print("Invalid owner/repo format. Expected 'owner/repo'.")
        sys.exit(1)

    json_url = f"https://api.github.com/repos/{owner}/{repo_name}/releases/latest"
    resp = requests.get(json_url)
    resp.raise_for_status()
    js = resp.json()
    try:
        release = next(
            x for x in js["assets"] if file_selector.lower() in x["name"].lower()
        )
    except StopIteration:
        names = "\n".join([x["name"] for x in js["assets"]])
        print(f"{file_selector=} not found. Available options:\n{names}")
        sys.exit(1)
    return release["browser_download_url"], release["name"]


def get_json_release(json_url, json_selector):
    resp = requests.get(json_url)
    resp.raise_for_status()
    js = resp.json()

    jsonpath_expr = parse(json_selector)

    try:
        url = next(match.value for match in jsonpath_expr.find(js))
    except StopIteration:
        print(f"Could not locate {json_selector=} in")
        pprint.pprint(js)
        sys.exit(1)

    fname = url.split("/")[-1]

    return url, fname


def get_html_release(url, regex_selector):
    resp = requests.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    pattern = re.compile(regex_selector)

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if pattern.search(href):
            url = url.rsplit("/", 1)[0] + "/" + href
            fname = href.split("/")[-1]
            return url, fname

    print(f"Could not locate a link containing {regex_selector=} in HTML page.")
    sys.exit(1)


def download_file(url, path):
    print(f"Downloading {url} to {path}")
    resp = requests.get(url)
    resp.raise_for_status()
    with open(path, "wb") as fil:
        fil.write(resp.content)


def infer_package_name_version_from_url(
    url: str,
) -> tuple[str, str] | tuple[None, None]:
    """
    Infer package name and package version by the file name in the URL
    """
    try:
        inferred_package = rpm.package(url.split("/")[-1])
        return inferred_package.name, inferred_package.version
    except (IndexError, rpm.RpmError):
        return None, None


def infer_package_name_version_from_first_kb(
    url: str,
) -> tuple[str, str] | tuple[None, None]:
    """
    Infer package name and package version by downloading the first kB from an URL and inspecting the header of the RPM file with the `rpm` command
    """
    temp_path = Path(tempfile.gettempdir()) / Path(Path(url).name).with_suffix(".tmp")
    with open(temp_path, "wb") as temp_file:
        temp_file.write(requests.get(url, headers={"Range": "bytes=0-1024"}).content)

    try:
        result = subprocess.run(
            f'rpm -qp --queryformat "%{{NAME}} %{{VERSION}}" {temp_path}',
            shell=True,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        if result:
            name, version = result.split()
            return name, version

    except subprocess.CalledProcessError:
        pass

    finally:
        temp_path.unlink(missing_ok=True)

    return None, None


def main():
    args = parse_args()
    endpoint = args.endpoint
    redownload = args.redownload
    reinstall = args.reinstall
    directory = Path(args.directory)

    if endpoint == "preset":
        preset = presets[args.preset]
        endpoint = preset["endpoint"]
        args = argparse.Namespace(**preset)

    if endpoint == "github":
        url, fname = get_github_release(args.repo, args.file_selector)
    elif endpoint == "json":
        url, fname = get_json_release(args.json_url, args.json_selector)
    elif endpoint == "url":
        resp = requests.head(args.url, allow_redirects=True)
        url = resp.url
        fname = url.split("/")[-1]
    elif endpoint == "html":
        url, fname = get_html_release(args.url, args.regex_selector)
    else:
        print(f"Unknown endpoint type. Choose from {endpoints + ["preset"]}")
        sys.exit(1)

    package_name, rpm_version = infer_package_name_version_from_url(url)
    if not package_name:
        package_name, rpm_version = infer_package_name_version_from_first_kb(url)

    if package_name and rpm_version is not None:
        installed_versions = subprocess.run(
            f"rpm -q {package_name} --queryformat '%{{VERSION}}'",
            shell=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if len(installed_versions) == 1:
            installed_version = installed_versions[0]
            if not installed_version.endswith("not installed"):
                print(f"{package_name} {installed_version} already installed")
                if Version(installed_version) < Version(rpm_version):
                    install = True
                elif Version(installed_version) == Version(rpm_version):
                    if reinstall:
                        install = True
                    else:
                        print(
                            f"Version {installed_version} already installed; not installing."
                        )
                        install = False
                else:
                    print(
                        f"Installed version {installed_version} newer than downloaded version {rpm_version}; not installing."
                    )
                    install = False
            else:
                install = True
        else:
            install = True
    else:
        install = True

    if install:
        path = directory / fname
        if path.exists() and not redownload:
            print(f"{fname} already exists. Skipping download.")
        else:
            download_file(url, path)

        cmd = ["sudo", "-S", "dnf", "install", "--assumeyes", str(path)]
        cmd_s = shlex.join(cmd)
        print("Installing ...")
        print(cmd_s)
        subprocess.call(cmd)


if __name__ == "__main__":
    main()
