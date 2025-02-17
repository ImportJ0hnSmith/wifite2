#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import signal

from .dependency import Dependency
from .ip import Ip
from .iw import Iw
from ..config import Configuration
from ..util.color import Color
from ..util.process import Process


class AirmonIface(object):
    def __init__(self, phy, interface, driver, chipset):
        self.phy = phy
        self.interface = interface
        self.driver = driver
        self.chipset = chipset

    # Max length of fields.
    # Used for printing a table of interfaces.
    INTERFACE_LEN = 12
    PHY_LEN = 6
    DRIVER_LEN = 20
    CHIPSET_LEN = 30

    def __str__(self):
        """ Colored string representation of interface """
        s = ''
        s += Color.s('{G}%s' % self.interface.ljust(self.INTERFACE_LEN))
        s += Color.s('{W}%s' % self.phy.ljust(self.PHY_LEN))
        s += Color.s('{C}%s' % self.driver.ljust(self.DRIVER_LEN))
        s += Color.s('{W}%s' % self.chipset.ljust(self.CHIPSET_LEN))
        return s

    @staticmethod
    def menu_header():
        """ Colored header row for interfaces """
        s = '    ' + 'Interface'.ljust(AirmonIface.INTERFACE_LEN)
        s += 'PHY'.ljust(AirmonIface.PHY_LEN)
        s += 'Driver'.ljust(AirmonIface.DRIVER_LEN)
        s += 'Chipset'.ljust(AirmonIface.CHIPSET_LEN)
        s += '\n'
        s += '-' * \
             (AirmonIface.INTERFACE_LEN + AirmonIface.PHY_LEN + AirmonIface.DRIVER_LEN + AirmonIface.CHIPSET_LEN + 3)
        return s


class Airmon(Dependency):
    """ Wrapper around the 'airmon-ng' program """
    dependency_required = True
    dependency_name = 'airmon-ng'
    dependency_url = 'https://www.aircrack-ng.org/install.html'

    chipset_table = 'https://wikidevi.com/wiki/Wireless_adapters/Chipset_table'

    base_interface = None
    killed_network_manager = False

    # Drivers that need to be manually put into monitor mode
    BAD_DRIVERS = ['rtl8821au']
    # see if_arp.h
    ARPHRD_ETHER = 1  # managed
    ARPHRD_IEEE80211_RADIOTAP = 803  # monitor

    def __init__(self):
        self.refresh()

    def refresh(self):
        """ Get airmon-recognized interfaces """
        self.interfaces = Airmon.get_interfaces()

    def print_menu(self):
        """ Prints menu """
        print((AirmonIface.menu_header()))
        for idx, iface in enumerate(self.interfaces, start=1):
            Color.pl(' {G}%d{W}. %s' % (idx, iface))

    def get(self, index):
        """ Gets interface at index (starts at 1) """
        if type(index) is str:
            index = int(index)
        return self.interfaces[index - 1]

    @staticmethod
    def get_interfaces():
        """Returns List of AirmonIface objects known by airmon-ng"""
        interfaces = []
        p = Process('airmon-ng')
        for line in p.stdout().split('\n'):
            # [PHY ]IFACE DRIVER CHIPSET
            airmon_re = re.compile(r'^(?:([^\t]*)\t+)?([^\t]*)\t+([^\t]*)\t+([^\t]*)$')
            matches = airmon_re.match(line)
            if not matches:
                continue

            phy, interface, driver, chipset = matches.groups()
            if phy in ['PHY', 'Interface']:
                continue  # Header

            if len(interface.strip()) == 0:
                continue

            interfaces.append(AirmonIface(phy, interface, driver, chipset))

        return interfaces

    @staticmethod
    def get_iface_info(interface_name):
        """
        Get interface info (driver, chipset), based on interface name.
        Returns an AirmonIface if interface name is found by airmon-ng or None
        """
        return next((iface for iface in Airmon.get_interfaces() if iface.interface == interface_name), None)

    @staticmethod
    def start_bad_driver(iface):
        """
        Manually put interface into monitor mode (no airmon-ng or vif).
        Fix for bad drivers like the rtl8812AU.
        """
        Ip.down(iface)
        Iw.mode(iface, 'monitor')
        Ip.up(iface)

        # /sys/class/net/wlan0/type
        iface_type_path = os.path.join('/sys/class/net', iface, 'type')
        if os.path.exists(iface_type_path):
            with open(iface_type_path, 'r') as f:
                if int(f.read()) == Airmon.ARPHRD_IEEE80211_RADIOTAP:
                    return iface

        return None

    @staticmethod
    def stop_bad_driver(iface):
        """
        Manually put interface into managed mode (no airmon-ng or vif).
        Fix for bad drivers like the rtl8812AU.
        """
        Ip.down(iface)
        Iw.mode(iface, 'managed')
        Ip.up(iface)

        # /sys/class/net/wlan0/type
        iface_type_path = os.path.join('/sys/class/net', iface, 'type')
        if os.path.exists(iface_type_path):
            with open(iface_type_path, 'r') as f:
                if int(f.read()) == Airmon.ARPHRD_ETHER:
                    return iface

        return None

    @staticmethod
    def start(iface):
        """
            Starts an interface (iface) in monitor mode
            Args:
                iface - The interface to start in monitor mode
                        Either an instance of AirmonIface object,
                        or the name of the interface (string).
            Returns:
                Name of the interface put into monitor mode.
            Throws:
                Exception - If an interface can't be put into monitor mode
        """
        # Get interface name from input
        if type(iface) == AirmonIface:
            iface_name = iface.interface
            driver = iface.driver
        else:
            iface_name = iface
            driver = None

        # Remember this as the 'base' interface.
        Airmon.base_interface = iface_name

        Color.p('{+} enabling {G}monitor mode{W} on {C}%s{W}... ' % iface_name)

        airmon_output = Process(['airmon-ng', 'start', iface_name]).stdout()

        enabled_iface = Airmon._parse_airmon_start(airmon_output)

        if enabled_iface is None and driver in Airmon.BAD_DRIVERS:
            Color.p('{O}"bad driver" detected{W} ')
            enabled_iface = Airmon.start_bad_driver(iface_name)

        if enabled_iface is None:
            Color.pl('{R}failed{W}')

        monitor_interfaces = Iw.get_interfaces(mode='monitor')

        # Assert that there is an interface in monitor mode
        if len(monitor_interfaces) == 0:
            Color.pl('{R}failed{W}')
            raise Exception('Cannot find any interfaces in monitor mode')

        # Assert that the interface enabled by airmon-ng is in monitor mode
        if enabled_iface not in monitor_interfaces:
            Color.pl('{R}failed{W}')
            raise Exception('Cannot find %s with type:monitor' % enabled_iface)

        # No errors found; the device 'enabled_iface' was put into Mode:Monitor.
        Color.pl('{G}enabled {C}%s{W}' % enabled_iface)

        return enabled_iface

    @staticmethod
    def _parse_airmon_start(airmon_output):
        """Find the interface put into monitor mode (if any)"""

        # airmon-ng output: (mac80211 monitor mode vif enabled for [phy10]wlan0 on [phy10]wlan0mon)
        enabled_re = re.compile(r'.*\(mac80211 monitor mode (?:vif )?enabled (?:for [^ ]+ )?on (?:\[\w+])?(\w+)\)?.*')
        lines = airmon_output.split('\n')

        for index, line in enumerate(lines):
            if matches := enabled_re.match(line):
                return matches[1]
            if "monitor mode enabled" in line:
                return re.sub(r'\s+', ' ', lines[index - 1]).split(' ')[1]

        return None

    @staticmethod
    def stop(iface):
        Color.p('{!}{W} Disabling {O}monitor{W} mode on {R}%s{W}...\n' % iface)

        airmon_output = Process(['airmon-ng', 'stop', iface]).stdout()

        (disabled_iface, enabled_iface) = Airmon._parse_airmon_stop(airmon_output)

        if not disabled_iface and iface in Airmon.BAD_DRIVERS:
            Color.p('{!} {O}"bad driver" detected{W} ')
            disabled_iface = Airmon.stop_bad_driver(iface)

        if disabled_iface:
            Color.pl('{+}{W} Disabled monitor mode on {G}%s{W}' % disabled_iface)
        else:
            Color.pl('{!} {O}Could not disable {R}%s{W}' % iface)

        return disabled_iface, enabled_iface

    @staticmethod
    def _parse_airmon_stop(airmon_output):
        """Find the interface taken out of into monitor mode (if any)"""

        # airmon-ng 1.2rc2 output: (mac80211 monitor mode vif enabled for [phy10]wlan0 on [phy10]wlan0mon)
        disabled_re = re.compile(r'\s*\(mac80211 monitor mode (?:vif )?disabled for (?:\[\w+])?(\w+)\)\s*')

        # airmon-ng 1.2rc1 output: wlan0mon (removed)
        removed_re = re.compile(r'([a-zA-Z\d]+).*\(removed\)')

        # Enabled interface: (mac80211 station mode vif enabled on [phy4]wlan0)
        enabled_re = re.compile(r'\s*\(mac80211 station mode (?:vif )?enabled on (?:\[\w+])?(\w+)\)\s*')

        disabled_iface = None
        enabled_iface = None
        for line in airmon_output.split('\n'):
            if matches := disabled_re.match(line):
                disabled_iface = matches.group(1)

            if matches := removed_re.match(line):
                disabled_iface = matches.group(1)

            if matches := enabled_re.match(line):
                enabled_iface = matches.group(1)

        return disabled_iface, enabled_iface

    @staticmethod
    def ask():
        """
        Asks user to define which wireless interface to use.
        Does not ask if:
            1. There is already an interface in monitor mode, or
            2. There is only one wireless interface (automatically selected).
        Puts selected device into Monitor Mode.
        """

        Airmon.terminate_conflicting_processes()

        Color.p('\n{+} Looking for {C}wireless interfaces{W}...')
        monitor_interfaces = Iw.get_interfaces(mode='monitor')
        if len(monitor_interfaces) == 1:
            # Assume we're using the device already in monitor mode
            iface = monitor_interfaces[0]
            Color.clear_entire_line()
            Color.pl('{+} Using {G}%s{W} already in monitor mode' % iface)
            Airmon.base_interface = None
            return iface

        Color.clear_entire_line()
        Color.p('{+} Checking {C}airmon-ng{W}...')
        a = Airmon()
        count = len(a.interfaces)
        if count == 0:
            # No interfaces found
            Color.pl('\n{!} {O}airmon-ng did not find {R}any{O} wireless interfaces')
            Color.pl('{!} {O}Make sure your wireless device is connected')
            Color.pl('{!} {O}See {C}http://www.aircrack-ng.org/doku.php?id=airmon-ng{O} for more info{W}')
            raise Exception('airmon-ng did not find any wireless interfaces')

        Color.clear_entire_line()
        a.print_menu()

        Color.pl('')

        if count == 1:
            # Only one interface, assume this is the one to use
            choice = 1
        else:
            # Multiple interfaces found
            Color.p('{+} Select wireless interface ({G}1-%d{W}): ' % count)
            choice = input()

        iface = a.get(choice)

        if a.get(choice).interface in monitor_interfaces:
            Color.pl('{+} {G}%s{W} is already in monitor mode' % iface.interface)
        else:
            iface.interface = Airmon.start(iface)
        return iface.interface

    @staticmethod
    def terminate_conflicting_processes():
        """ Deletes conflicting processes reported by airmon-ng """

        airmon_output = Process(['airmon-ng', 'check']).stdout()

        # Conflicting process IDs and names
        pid_pnames = []

        # 2272    dhclient
        # 2293    NetworkManager
        pid_pname_re = re.compile(r'^\s*(\d+)\s*([a-zA-Z\d_\-]+)\s*$')
        for line in airmon_output.split('\n'):
            if match := pid_pname_re.match(line):
                pid = match[1]
                pname = match[2]
                pid_pnames.append((pid, pname))

        if not pid_pnames:
            return

        if not Configuration.kill_conflicting_processes:
            # Don't kill processes, warn user
            names_and_pids = ', '.join([
                '{R}%s{O} (PID {R}%s{O})' % (pname, pid)
                for pid, pname in pid_pnames
            ])
            Color.pl('{!} {O}Conflicting processes: %s' % names_and_pids)
            Color.pl('{!} {O}If you have problems: {R}kill -9 PID{O} or re-run wifite with {R}--kill{O}{W}')
            return

        Color.pl('{!} {O}Killing {R}%d {O}conflicting processes' % len(pid_pnames))
        for pid, pname in pid_pnames:
            if pname == 'NetworkManager' and Process.exists('systemctl'):
                Color.pl('{!} {O}stopping NetworkManager ({R}systemctl stop NetworkManager{O})')
                # Can't just pkill NetworkManager; it's a service
                Process(['systemctl', 'stop', 'NetworkManager']).wait()
                Airmon.killed_network_manager = True
            elif pname == 'network-manager' and Process.exists('service'):
                Color.pl('{!} {O}stopping network-manager ({R}service network-manager stop{O})')
                # Can't just pkill network manager; it's a service
                Process(['service', 'network-manager', 'stop']).wait()
                Airmon.killed_network_manager = True
            elif pname == 'avahi-daemon' and Process.exists('service'):
                Color.pl('{!} {O}stopping avahi-daemon ({R}service avahi-daemon stop{O})')
                # Can't just pkill avahi-daemon; it's a service
                Process(['service', 'avahi-daemon', 'stop']).wait()
            else:
                Color.pl('{!} {R}Terminating {O}conflicting process {R}%s{O} (PID {R}%s{O})' % (pname, pid))
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except Exception:
                    pass

    @staticmethod
    def put_interface_up(iface):
        Color.p('{!}{W} Putting interface {R}%s{W} {G}up{W}...\n' % iface)
        Ip.up(iface)
        Color.pl('{+}{W} Done !')

    @staticmethod
    def start_network_manager():
        Color.p('{!} {O}restarting {R}NetworkManager{O}...')

        if Process.exists('service'):
            cmd = 'service networkmanager start'
            proc = Process(cmd)
            (out, err) = proc.get_output()
            if proc.poll() != 0:
                Color.pl(' {R}Error executing {O}%s{W}' % cmd)
                if out is not None and out.strip() != '':
                    Color.pl('{!} {O}STDOUT> %s{W}' % out)
                if err is not None and err.strip() != '':
                    Color.pl('{!} {O}STDERR> %s{W}' % err)
            else:
                Color.pl(' {G}done{W} ({C}%s{W})' % cmd)
                return

        if Process.exists('systemctl'):
            cmd = 'systemctl start NetworkManager'
            proc = Process(cmd)
            (out, err) = proc.get_output()
            if proc.poll() != 0:
                Color.pl(' {R}Error executing {O}%s{W}' % cmd)
                if out is not None and out.strip() != '':
                    Color.pl('{!} {O}STDOUT> %s{W}' % out)
                if err is not None and err.strip() != '':
                    Color.pl('{!} {O}STDERR> %s{W}' % err)
            else:
                Color.pl(' {G}done{W} ({C}%s{W})' % cmd)
                return
        else:
            Color.pl(' {R}cannot restart NetworkManager: {O}systemctl{R} or {O}service{R} not found{W}')


if __name__ == '__main__':
    stdout = '''
Found 2 processes that could cause trouble.
If airodump-ng, aireplay-ng or airtun-ng stops working after
a short period of time, you may want to run 'airmon-ng check kill'

  PID Name
 5563 avahi-daemon
 5564 avahi-daemon

PHY	Interface	Driver		Chipset

phy0	wlx00c0ca4ecae0	rtl8187		Realtek Semiconductor Corp. RTL8187
Interface 15mon is too long for linux so it will be renamed to the old style (wlan#) name.

                (mac80211 monitor mode vif enabled on [phy0]wlan0mon
                (mac80211 station mode vif disabled for [phy0]wlx00c0ca4ecae0)
    '''
    start_iface = Airmon._parse_airmon_start(stdout)
    print(('start_iface from stdout:', start_iface))

    Configuration.initialize(False)
    iface = Airmon.ask()
    (disabled_iface, enabled_iface) = Airmon.stop(iface)
    print(('Disabled:', disabled_iface))
    print(('Enabled:', enabled_iface))
