#!/usr/bin/env python -u
##############################################################################
#
# Copyright (c) 2007 Agendaless Consulting and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the BSD-like license at
# http://www.repoze.org/LICENSE.txt.  A copy of the license should accompany
# this distribution.  THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL
# EXPRESS OR IMPLIED WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND
# FITNESS FOR A PARTICULAR PURPOSE
#
##############################################################################

# A event listener meant to be subscribed to TICK_60 (or TICK_5)
# events, which restarts any processes that are children of
# supervisord that consume "too much" memory.  Performs horrendous
# screenscrapes of ps output.  Works on Linux and OS X (Tiger/Leopard)
# as far as I know.

# A supervisor config snippet that tells supervisor to use this script
# as a listener is below.
#
# [eventlistener:uptimemon]
# command=python uptimemon.py [options]
# events=TICK_60

doc = """\
uptimemon.py [-p processname=byte_size] [-g groupname=byte_size]
          [-u uptime_limit] [-n uptimemon_name]

Options:

-p -- specify a process_name=uptime_limit pair.  Restart the supervisor
      process named 'process_name' when it up more than uptime_limit.
      If this process is in a group, it can be specified using
      the 'process_name:group_name' syntax.

-g -- specify a group_name=uptime_limit pair.  Restart any process in this group
      when it up more than uptime_limit.

-u -- specify a global uptime_limit.  Restart any child of the supervisord
      under which this runs if it up more than uptime_limit.
      seconds can be specified as plain integer values or a suffix-multiplied integer
      (e.g. 1m). Valid suffixes are m (minute), h (hour) and d (day).

-e -- exclude name of uptimemon supervisor eventlistener to avoid restart if you use -u option


The -p and -g options may be specified more than once, allowing for
specification of multiple groups and processes.

A sample invocation:

uptimemon.py -p program1=200s -p theprog:thegroup=10m -g thegroup=1h"
uptimemon.py -n uptimemon -u 1h"
"""

import os
import sys
import time
from collections import namedtuple
from superlance.compat import xmlrpclib

from supervisor import childutils
from supervisor.datatypes import byte_size, SuffixMultiplier

def usage():
    print(doc)
    sys.exit(255)

def shell(cmd):
    with os.popen(cmd) as f:
        return f.read()

class Uptimemon:
    def __init__(self, programs, groups, uptime_limit, name, rpc=None):
        self.programs = programs
        self.groups = groups
        self.uptime_limit = uptime_limit
        self.uptimemonName = name
        self.rpc = rpc
        self.stdin = sys.stdin
        self.stdout = sys.stdout
        self.stderr = sys.stderr
        self.pscommand = 'ps -ocurrentuptime= -p %s'
        self.pstreecommand = 'ps ax -o "pid= ppid= currentuptime="'

    def runforever(self, test=False):
        while 1:
            # we explicitly use self.stdin, self.stdout, and self.stderr
            # instead of sys.* so we can unit test this code
            headers, payload = childutils.listener.wait(self.stdin, self.stdout)

            if not headers['eventname'].startswith('TICK'):
                # do nothing with non-TICK events
                childutils.listener.ok(self.stdout)
                if test:
                    break
                continue

            status = []
            if self.programs:
                keys = sorted(self.programs.keys())
                status.append(
                    'Checking programs %s' % ', '.join(
                    [ '%s=%s' % (k, self.programs[k]) for k in keys ])
                    )

            if self.groups:
                keys = sorted(self.groups.keys())
                status.append(
                    'Checking groups %s' % ', '.join(
                    [ '%s=%s' % (k, self.groups[k]) for k in keys ])
                    )
            if self.uptime_limit is not None:
                status.append('Checking uptime_limit=%s' % self.uptime_limit)

            self.stderr.write('\n'.join(status) + '\n')

            infos = self.rpc.supervisor.getAllProcessInfo()

            for info in infos:
                pid = info['pid']
                name = info['name']
                group = info['group']
                pname = '%s:%s' % (group, name)

                if not pid:
                    # ps throws an error in this case (for processes
                    # in standby mode, non-auto-started).
                    continue

                currentuptime = self.calc_currentuptime(pname)
                if currentuptime is None:
                    # no such pid (deal with race conditions) or
                    # currentuptime couldn't be calculated for other reasons
                    continue

                for n in name, pname:
                    if n in self.programs:
                        self.stderr.write('UPTIME of %s is %ssec / %ssec\n' % (pname, currentuptime, self.programs[name]))
                        if  currentuptime > self.programs[name]:
                            self.restart(pname, currentuptime)
                            continue

                if group in self.groups:
                    self.stderr.write('UPTIME of %s is %ssec / %ssec\n' % (pname, currentuptime, self.groups[group]))
                    if currentuptime > self.groups[group]:
                        self.restart(pname, currentuptime)
                        continue

                if self.uptime_limit is not None:
                    self.stderr.write('UPTIME of %s is %ssec / %ssec\n' % (pname, currentuptime,self.uptime_limit))
                    if currentuptime > self.uptime_limit:
                        self.restart(pname, currentuptime)
                        continue

            self.stderr.flush()
            childutils.listener.ok(self.stdout)
            if test:
                break

    def calc_currentuptime(self, name):
        info = self.rpc.supervisor.getProcessInfo(name)
        uptime = info['now'] - info['start'] #uptime in seconds
        return uptime


    def restart(self, name, currentuptime):
        procgroup, procname = name.split(":")
        self.stderr.write('aaaaaaaaaaaaaaa name: %s uptimemonName: %s   procgroup: %s   procname: %s\n' % (name, self.uptimemonName,procgroup,procname))
        if procname != self.uptimemonName:
            info = self.rpc.supervisor.getProcessInfo(name)
            self.stderr.write('Restarting %s\n' % name)
            #uptimemonId = self.uptimemonName and " [%s]" % self.uptimemonName or ""
        
            try:
                self.rpc.supervisor.stopProcess(name)
            except xmlrpclib.Fault as e:
                msg = ('Failed to stop process %s (UPTIME %ssec), exiting: %s' %
                       (name, currentuptime, e))
                self.stderr.write(str(msg))
                raise

            try:
                self.rpc.supervisor.startProcess(name)
            except xmlrpclib.Fault as e:
                msg = ('Failed to start process %s after stopping it, '
                       'exiting: %s' % (name, e))
                self.stderr.write(str(msg))
                raise


def parse_nametime(option, value):
    try:
        name, time = value.split('=')
    except ValueError:
        print('Unparseable value %r for %r' % (value, option))
        usage()
    time = parse_time(option, time)
    return name, time

def parse_time(option, value):
    try:
        time = seconds_time(value)
    except:
        print('Unparseable byte_size in %r for %r' % (value, option))
        usage()
    return time


seconds_time = SuffixMultiplier({'s': 1,
                                 'm': 60,
                                 'h': 60 * 60,
                                 'd': 60 * 60 * 24
                                 })

def parse_seconds(option, value):
    try:
        seconds = seconds_time(value)
    except:
        print('Unparseable value for time in %r for %s' % (value, option))
        usage()
    return seconds

def uptimemon_from_args(arguments):
    import getopt
    short_args = "hp:g:u:e:"
    long_args = [
        "help",
        "program=",
        "group=",
        "uptime=",
        "exclude=",
        ]

    if not arguments:
        return None
    try:
        opts, args = getopt.getopt(arguments, short_args, long_args)
    except:
        return None

    programs = {}
    groups = {}
    uptime_limit = None
    name = None

    for option, value in opts:

        if option in ('-h', '--help'):
            return None

        if option in ('-p', '--program'):
            name, time = parse_nametime(option, value)
            programs[name] = time

        if option in ('-g', '--group'):
            name, time = parse_nametime(option, value)
            groups[name] = time

        if option in ('-u', '--uptime'):
            uptime_limit = parse_seconds(option, value)

        if option in ('-e', '--exclude'):
            name = value

    uptimemon = Uptimemon(programs=programs,
                    groups=groups,
                    uptime_limit=uptime_limit,
                    name=name)
    return uptimemon

def main():
    uptimemon = uptimemon_from_args(sys.argv[1:])
    if uptimemon is None:
        # something went wrong or -h has been given
        usage()
    uptimemon.rpc = childutils.getRPCInterface(os.environ)
    uptimemon.runforever()

if __name__ == '__main__':
    main()
