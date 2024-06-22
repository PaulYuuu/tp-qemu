#!/bin/env python

import re
import sys
from argparse import ArgumentParser

import commands


class GuestAgentPkg:
    """
    A guest agent package class
    """

    def __init__(
        self,
        build_tag,
        build_name,
        url,
        authtype="",
        server="",
        topdir="",
        weburl="",
        topurl="",
    ):
        self.build_tag = build_tag
        self.build_name = build_name
        self.server_url = url
        self.authtype = authtype
        self.server = server
        self.topdir = topdir
        self.weburl = weburl
        self.topurl = topurl

    def _run_brew_cmd(self, cmd):
        brew_cmd = "brew "
        if self.authtype:
            brew_cmd += f"--authtype={self.authtype} "
        if self.server:
            brew_cmd += f"--server={self.server} "
        if self.topdir:
            brew_cmd += f"--topdir={self.topdir} "
        if self.weburl:
            brew_cmd += f"--weburl={self.weburl} "
        if self.topurl:
            brew_cmd += f"--topurl={self.topurl} "
        brew_cmd += cmd
        (status, output) = commands.getstatusoutput(brew_cmd)
        if status:
            raise Exception(f"the cmd {brew_cmd} didn't run successfully")
        return (status, output)

    def get_latest_build(self):
        cmd = f"latest-build {self.build_tag} {self.build_name}"
        (status, output) = self._run_brew_cmd(cmd)
        for line in output.splitlines():
            if self.build_name in line:
                return line.split()[0]
        raise Exception("didn't get latest build name")

    def get_build_url(self):
        build_name = self.get_latest_build()
        cmd = f"buildinfo {build_name} | grep msi"
        (status, output) = self._run_brew_cmd(cmd)
        url_list = []
        for package in output.splitlines():
            url = re.sub(r"/mnt/redhat", self.server_url, package)
            url_list.append(url)
        return url_list

    def download_package(self):
        url_list = self.get_build_url()
        if not url_list:
            raise Exception("url list is empty")
        cmd = "wget %s -P /tmp/"
        for url in url_list:
            (status, output) = commands.getstatusoutput(cmd % url)
            if status:
                raise Exception(f"the download from {url} didn't run successfully")
            print(f"\033[32m {url} download successfully\033[0m")


def parse_params(program):
    """
    parse the params passed to the application
    """
    parser = ArgumentParser(prog=program)
    option_list = [
        ("build_tag", "the tag of the build"),
        ("build_name", "the name of the build"),
    ]
    brew_conf_list = [
        ("-s", "--server", "url of XMLRPC server"),
        ("-a", "--authtype", "the type of authentication"),
        ("-t", "--topdir", "specify topdir"),
        ("-w", "--weburl", "url of the Koji web interface"),
        ("-T", "--topurl", "url for Koji file access"),
    ]
    for option in option_list:
        parser.add_argument(dest=option[0], help=option[1])
    for brew_conf in brew_conf_list:
        parser.add_argument(brew_conf[0], brew_conf[1], help=brew_conf[2])
    parser.add_argument(
        "-u",
        "--url",
        required=True,
        dest="download_url",
        help="the server url which we can download package",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_params(sys.argv[0])
    guestagent = GuestAgentPkg(
        args.build_tag,
        args.build_name,
        args.download_url,
        args.authtype,
        args.server,
        args.topdir,
        args.weburl,
        args.topurl,
    )
    guestagent.download_package()
