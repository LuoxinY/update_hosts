import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait, ALL_COMPLETED
from enum import Enum, unique

import fire
import requests
from cacheout import Cache
from faker import Faker
from ping3 import ping
from rich.progress import track
from rich.table import Table

from console import console
from dns_list import dns_service_list
from hosts import Hosts, HostsEntry
from lock import new_lock, acquire
from utils import is_ipv4, is_ipv6

f = Faker()


class UpdateHosts(object):
    _domain_list = [
        "github.com",
        "api.github.com",
        "support.github.com",
        "githubstatus.com",
        "polyfill.io",
        "google-analytics.com",
        "cloudfront.net",
        "gstatic.com",
        "fonts.gstatic.com",
        "github.githubassets.com",
        "camo.githubusercontent.com",
        "github.map.fastly.net",
        "github.global.ssl.fastly.net",
        "raw.githubusercontent.com",
        "user-images.githubusercontent.com",
        "collector.githubapp.com",
        "favicons.githubusercontent.com",
        "avatars0.githubusercontent.com",
        "avatars1.githubusercontent.com",
        "avatars2.githubusercontent.com",
        "avatars3.githubusercontent.com",
        "avatars4.githubusercontent.com",
        "avatars5.githubusercontent.com",
    ]

    __slots__ = [
        "hosts_path",
        "dns_cache",
        "check_cache",
        "domain_list",
        "max_workers",
        "check_type",
        "dns_query_cache",
        "dns_lock",
        "check_query_cache",
        "check_lock",
    ]

    def __init__(self):
        self.hosts_path = None
        self.max_workers = 5

        self.domain_list = self._domain_list

    def get_hosts(self):
        return Hosts(path=self.hosts_path)

    def set_hosts_path(self, hosts_path: str = None):
        self.hosts_path = hosts_path

    def set_max_workers(self, max_workers: int = 5):
        self.max_workers = max_workers

    def set_domain(self, domain_list=None):
        if domain_list is not None:
            if isinstance(domain_list, str):
                domain_list = domain_list.replace(" ", "").split(",")
                if len(domain_list) == 0:
                    console.print("[red]can not find domains[/red]")
                    return
            elif isinstance(domain_list, (list, tuple, set)):
                pass
            else:
                console.print("[red]invalid domain_list[/red]")
                return

            self.domain_list = domain_list

        self.domain_list = list(set(self.domain_list))

    def check_all(self, domain: str, ip_list: (set, tuple, list)):
        min_delay = None
        fastest_ip = None
        for ip in track(
            ip_list,
            description="check connectivity for [yellow]{}[/yellow]".format(domain),
            console=console,
            transient=True,
        ):
            try:
                delay = self.check(ip)

                if delay is not None and (min_delay is None or min_delay > delay):
                    min_delay = delay
                    fastest_ip = ip
            except OSError:
                pass
            except:
                console.print_exception()

        return fastest_ip

    def dns_query_all(self, domain: str):
        if domain.startswith("*."):
            domain = domain.replace("*.", "", 1)

        dns_cache = self.dns_cache.get(domain)
        if dns_cache is not None:
            return dns_cache

        ip_pool_dns = []

        console.print("will query dns [yellow]{}[/yellow]".format(domain))

        for dns_server in track(
            dns_service_list,
            description="dns query [yellow]{}[/yellow]".format(domain),
            console=console,
            transient=True,
        ):
            ip_list, cname_list = self.dns_query(dns_server, domain)
            ip_pool_dns.extend(ip_list)
            for cname in cname_list:
                self.dns_query_all(domain=cname)

        self.dns_cache.set(domain, list(set(ip_pool_dns)))

        return self.dns_cache.get(domain)

    def update_domain(self, domain: str, hosts: Hosts):
        console.print("update domain hosts [yellow]{}[/yellow] ......".format(domain))

        ip_list = self.dns_query_all(domain)

        if ip_list is None or len(ip_list) == 0:
            console.print("not query ip for domain [red]{}[/red]".format(domain))
            return

        console.print(
            "will check connectivity [yellow]{}[/yellow]([green]{}[/green])".format(
                domain, ",".join(ip_list)
            )
        )

        fastest_ip = self.check_all(domain, ip_list)
        if fastest_ip is None:
            console.print("not query ip for domain [red]{}[/red]".format(domain))
            return

        console.print(
            "will add hosts to cache [yellow]{}[yellow]([green]{}[/green])".format(
                domain, fastest_ip
            )
        )

        if is_ipv4(fastest_ip):
            entry_type = "ipv4"
        elif is_ipv6(fastest_ip):
            entry_type = "ipv6"
        else:
            entry_type = "comment"

        hosts.remove_all_matching(name=domain)
        hosts.add(
            [HostsEntry(entry_type=entry_type, address=fastest_ip, names=[domain]),]
        )

        console.print("update domain hosts [yellow]{}[/yellow] finished".format(domain))

    def update_dns(self, domain_list=None, agree: bool = False):
        self.set_domain(domain_list)

        console.print(
            "will check and update domains: [yellow]{}[/yellow] [[y/N]".format(
                " ".join(self.domain_list)
            ),
            end=": ",
        )

        if not agree:
            agree = console.input()
            if agree.lower() != "y":
                return
        else:
            console.print("[green]y[/green]")

        hosts = self.get_hosts()
        if hosts is None:
            return

        with ThreadPoolExecutor(self.max_workers) as thread_pool:
            all_task = [
                thread_pool.submit(self.update_domain, domain=domain, hosts=hosts)
                for domain in self.domain_list
            ]
            wait(all_task, return_when=ALL_COMPLETED)
            console.print("all domain update finish")

        # with Progress(
        #     TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
        #     BarColumn(bar_width=None),
        #     "[progress.percentage]{task.percentage:>3.1f}%",
        #     "•",
        #     DownloadColumn(),
        #     "•",
        #     TransferSpeedColumn(),
        #     "•",
        #     TimeRemainingColumn(),
        # ) as progress:
        #     with ThreadPoolExecutor(max_workers=4) as pool:
        #         for domain in self.domain_list:

        hosts.write()

        table = Table(title="Hosts File", show_header=True, header_style="bold magenta")
        table.add_column("domain", justify="center", style="magenta")
        table.add_column("ip", justify="center", style="cyan")

        for entry in hosts.entries:
            if isinstance(entry.names, (tuple, set, list)) and len(entry.names) > 0:
                table.add_row(entry.names[0], entry.address)

        console.print(table)


def update_dns(l=None, y: bool = False, p: str = "", c: str = None, m: int = 5):
    """
    update dns
    :param l: need check domain list
    :param y: is agree
    :param p: hosts file path
    :param c: check type, is not input will use ping
        check type:
            ping: ping
            hd: http delay
            hs: http speed
    :param m: max works
    :return:
    """
    u = UpdateHosts()
    u.set_hosts_path(p)
    u.set_max_workers(m)

    if c is None or c == "":
        pass
    elif c == "ping":
        u.set_check_type(CheckType.Ping)
    elif c == "hd":
        u.set_check_type(CheckType.HttpDelayed)
    elif c == "hs":
        u.set_check_type(CheckType.HttpSpeed)
    else:
        console.print("invalid check type({})".format(c), style="red")
        return

    u.update_dns(domain_list=l, agree=y)


if __name__ == "__main__":
    fire.Fire({"update": update_dns})
