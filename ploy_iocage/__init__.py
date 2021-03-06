from lazy import lazy
from ploy.common import BaseMaster, Executor, StartupScriptMixin
from ploy.config import BaseMassager, value_asbool
from ploy.plain import Instance as PlainInstance
from ploy.proxy import ProxyInstance
import logging
import re
import socket
import sys
import time


log = logging.getLogger('ploy_iocage')


class IocageError(Exception):
    pass


rc_startup = """#!/bin/sh
#
# BEFORE: DAEMON
# PROVIDE: ploy.startup_script
#
# ploy startup script

. /etc/rc.subr

name=ploy.startup_script
start_cmd=startup

startup() {

# Remove traces of ourself
# N.B.: Do NOT rm $0, it points to /etc/rc
##########################
  rm -f "/etc/rc.d/ploy.startup_script"

  test -e /etc/startup_script && /etc/startup_script || true
  test -e /etc/startup_script && chmod 0600 /etc/startup_script

}

run_rc_command "$1"
"""


class Instance(PlainInstance, StartupScriptMixin):
    sectiongroupname = 'ioc-instance'

    _id_regexp = re.compile('^[a-zA-Z0-9_]+$')

    @property
    def _tag(self):
        return self.config.get('iocage-tag', self.id)

    def validate_id(self, sid):
        if self._id_regexp.match(sid) is None:
            log.error("Invalid instance tag '%s'. An iocage instance tag may only contain letters, numbers and underscores." % sid)
            sys.exit(1)
        return sid

    def get_host(self):
        return self.config.get('host', self.config['ip'])

    def get_fingerprint(self):
        status = self._status()
        if status == 'unavailable':
            log.info("Instance '%s' unavailable", self.id)
            sys.exit(1)
        if status != 'running':
            log.info("Instance state: %s", status)
            sys.exit(1)
        rc, out, err = self.master.iocage_admin('console', tag=self._tag, cmd='ssh-keygen -lf /etc/ssh/ssh_host_rsa_key.pub')
        info = out.split()
        return info[1]

    def get_massagers(self):
        return get_instance_massagers()

    def init_ssh_key(self, user=None):
        status = self._status()
        if status == 'unavailable':
            log.error("Instance '%s' unavailable", self.id)
            raise self.paramiko.SSHException()
        if status != 'running':
            log.error("Instance state: %s", status)
            raise self.paramiko.SSHException()
        if 'proxyhost' not in self.config:
            self.config['proxyhost'] = self.master.id
        if 'proxycommand' not in self.config:
            mi = self.master.instance
            self.config['proxycommand'] = self.proxycommand_with_instance(mi)
        return PlainInstance.init_ssh_key(self, user=user)

    def _status(self, jails=None):
        if jails is None:
            jails = self.master.iocage_admin('list')
        if self._tag not in jails:
            return 'unavailable'
        jail = jails[self._tag]
        status = jail['status']
        if len(status) != 2 or status[0] not in 'DIEBZ' or status[1] not in 'RAS':
            raise IocageError("Invalid jail status '%s' for '%s'" % (status, self._tag))
        if status[1] == 'R':
            return 'running'
        elif status[1] == 'S':
            return 'stopped'
        raise IocageError("Don't know how to handle mounted but not running jail '%s'" % self._tag)

    def status(self):
        try:
            jails = self.master.iocage_admin('list')
        except IocageError as e:
            log.error("Can't get status of jails: %s", e)
            return
        status = self._status(jails)
        if status == 'unavailable':
            log.info("Instance '%s' unavailable", self.id)
            return
        if status != 'running':
            log.info("Instance state: %s", status)
            return
        log.info("Instance running.")
        log.info("Instances jail id: %s" % jails[self._tag]['jid'])
        if self._tag != self.id:
            log.info("Instances jail tag: %s" % self._tag)
        log.info("Instances jail ip: %s" % jails[self._tag]['ip'])

    def start(self, overrides=None):
        jails = self.master.iocage_admin('list')
        status = self._status(jails)
        startup_script = None
        if status == 'unavailable':
            startup_script = self.startup_script(overrides=overrides)
            log.info("Creating instance '%s'", self.id)
            if 'ip' not in self.config:
                log.error("No IP address set for instance '%s'", self.id)
                sys.exit(1)
            try:
                self.master.iocage_admin(
                    'create',
                    tag=self._tag,
                    ip=self.config['ip'],
                    jailtype=self.config.get('jailtype'))
            except IocageError as e:
                for line in e.args[0].splitlines():
                    log.error(line)
                sys.exit(1)
            jails = self.master.iocage_admin('list')
            jail = jails.get(self._tag)
            startup_dest = '%s/etc/startup_script' % jail['root']
            rc, out, err = self.master._exec(
                'sh', '-c', 'cat - > "%s"' % startup_dest,
                stdin=startup_script)
            if rc != 0:
                log.error("Startup script creation failed.")
                log.error(err)
                sys.exit(1)
            rc, out, err = self.master._exec("chmod", "0700", startup_dest)
            if rc != 0:
                log.error("Startup script chmod failed.")
                log.error(err)
                sys.exit(1)
            rc_startup_dest = '%s/etc/rc.d/ploy.startup_script' % jail['root']
            rc, out, err = self.master._exec(
                'sh', '-c', 'cat - > "%s"' % rc_startup_dest,
                stdin=rc_startup)
            if rc != 0:
                log.error("Startup rc script creation failed.")
                log.error(err)
                sys.exit(1)
            rc, out, err = self.master._exec("chmod", "0700", rc_startup_dest)
            if rc != 0:
                log.error("Startup rc script chmod failed.")
                log.error(err)
                sys.exit(1)
            status = self._status(jails)
        if status != 'stopped':
            log.info("Instance state: %s", status)
            log.info("Instance already started")
            return True

        mounts = []
        for mount in self.config.get('mounts', []):
            src = mount['src'].format(
                zfs=self.master.zfs,
                tag=self._tag)
            dst = mount['dst'].format(
                tag=self._tag)
            create_mount = mount.get('create', False)
            mounts.append(dict(src=src, dst=dst, ro=mount.get('ro', False)))
            if create_mount:
                rc, out, err = self.master._exec("mkdir", "-p", src)
                if rc != 0:
                    log.error("Couldn't create source directory '%s' for mountpoint '%s'." % src, mount['src'])
                    log.error(err)
                    sys.exit(1)
        if mounts:
            jail = jails.get(self._tag)
            jail_fstab = '/etc/fstab.%s' % self._tag
            jail_root = jail['root'].rstrip('/')
            log.info("Setting up mount points")
            rc, out, err = self.master._exec("head", "-n", "1", jail_fstab)
            fstab = out.splitlines()
            fstab = fstab[:1]
            fstab.append('# mount points from ploy')
            for mount in mounts:
                self.master._exec(
                    "mkdir", "-p", "%s%s" % (jail_root, mount['dst']))
                if mount['ro']:
                    mode = 'ro'
                else:
                    mode = 'rw'
                fstab.append('%s %s%s nullfs %s 0 0' % (mount['src'], jail_root, mount['dst'], mode))
            fstab.append('')
            rc, out, err = self.master._exec(
                'sh', '-c', 'cat - > "%s"' % jail_fstab,
                stdin='\n'.join(fstab))
        if startup_script:
            log.info("Starting instance '%s' with startup script, this can take a while.", self.id)
        else:
            log.info("Starting instance '%s'", self.id)
        try:
            self.master.iocage_admin(
                'start',
                tag=self._tag)
        except IocageError as e:
            for line in e.args[0].splitlines():
                log.error(line)
            sys.exit(1)

    def stop(self, overrides=None):
        status = self._status()
        if status == 'unavailable':
            log.info("Instance '%s' unavailable", self.id)
            return
        if status != 'running':
            log.info("Instance state: %s", status)
            log.info("Instance not stopped")
            return
        log.info("Stopping instance '%s'", self.id)
        self.master.iocage_admin('stop', tag=self._tag)
        log.info("Instance stopped")

    def terminate(self):
        jails = self.master.iocage_admin('list')
        status = self._status(jails)
        if self.config.get('no-terminate', False):
            log.error("Instance '%s' is configured not to be terminated.", self.id)
            return
        if status == 'unavailable':
            log.info("Instance '%s' unavailable", self.id)
            return
        if status == 'running':
            log.info("Stopping instance '%s'", self.id)
            self.master.iocage_admin('stop', tag=self._tag)
        if status != 'stopped':
            log.info('Waiting for jail to stop')
            while status != 'stopped':
                jails = self.master.iocage_admin('list')
                status = self._status(jails)
                sys.stdout.write('.')
                sys.stdout.flush()
                time.sleep(1)
            print
        log.info("Terminating instance '%s'", self.id)
        self.master.iocage_admin('destroy', tag=self._tag)
        log.info("Instance terminated")


class ZFS_FS(object):
    def __init__(self, zfs, tag, config):
        self._tag = tag
        self.zfs = zfs
        self.config = config
        mp_args = (
            "zfs", "get", "-Hp", "-o", "property,value",
            "mountpoint", self['path'])
        rc, rout, rerr = self.zfs.master._exec(*mp_args)
        if rc != 0 and self.config.get('create', False):
            args = ['zfs', 'create']
            for k, v in self.config.items():
                if not k.startswith('set-'):
                    continue
                args.append("-o '%s=%s'" % (k[4:], v))
            args.append(self['path'])
            rc, out, err = self.zfs.master._exec(*args)
            if rc != 0:
                log.error(
                    "Couldn't create zfs filesystem '%s' at '%s'." % (
                        self._tag, self['path']))
                log.error(err)
                sys.exit(1)
        rc, out, err = self.zfs.master._exec(*mp_args)
        if rc == 0:
            info = out.strip().split('\t')
            assert info[0] == 'mountpoint'
            self.mountpoint = info[1]
            return
        log.error(
            "Trying to use non existing zfs filesystem '%s' at '%s'." % (
                self._tag, self['path']))
        sys.exit(1)

    def __getitem__(self, key):
        value = self.config[key]
        if key == 'path':
            return value.format(zfs=self.zfs)
        return value

    def __str__(self):
        return self.mountpoint


class ZFS(object):
    def __init__(self, master):
        self.master = master
        self.config = self.master.main_config.get('ioc-zfs', {})
        self._cache = {}

    def __getitem__(self, key):
        if key not in self._cache:
            self._cache[key] = ZFS_FS(self, key, self.config[key])
        return self._cache[key]


class IocageProxyInstance(ProxyInstance):
    def status(self):
        result = None
        hasstatus = hasattr(self._proxied_instance, 'status')
        if hasstatus:
            result = self._proxied_instance.status()
        if not hasstatus or self._status() == 'running':
            try:
                jails = self.master.iocage_admin('list')
            except IocageError as e:
                log.error("Can't get status of jails: %s", e)
                return result
            unknown = set(jails)
            for sid in sorted(self.master.instances):
                if sid == self.id:
                    continue
                instance = self.master.instances[sid]
                unknown.remove(instance._tag)
                status = instance._status(jails)
                sip = instance.config.get('ip', '')
                jip = jails.get(instance._tag, {}).get('ip', 'unknown ip')
                if status == 'running' and jip != sip:
                    sip = "%s != configured %s" % (jip, sip)
                log.info("%-20s %-15s %15s" % (sid, status, sip))
            for sid in sorted(unknown):
                jip = jails[sid].get('ip', 'unknown ip')
                log.warn("Unknown jail found: %-20s %15s" % (sid, jip))
        return result


class Master(BaseMaster):
    sectiongroupname = 'ioc-instance'
    instance_class = Instance
    _exec = None

    def __init__(self, *args, **kwargs):
        BaseMaster.__init__(self, *args, **kwargs)
        self.debug = self.master_config.get('debug-commands', False)
        if 'instance' not in self.master_config:
            instance = PlainInstance(self, self.id, self.master_config)
        else:
            instance = self.master_config['instance']
        if instance:
            self.instance = IocageProxyInstance(self, self.id, self.master_config, instance)
            self.instance.sectiongroupname = 'ioc-master'
            self.instances[self.id] = self.instance
        else:
            self.instance = None
        prefix_args = ()
        if self.master_config.get('sudo'):
            prefix_args = ('sudo',)
        if self._exec is None:
            self._exec = Executor(
                instance=self.instance, prefix_args=prefix_args)

    @lazy
    def zfs(self):
        return ZFS(self)

    @lazy
    def iocage_admin_binary(self):
        binary = self.master_config.get('iocage', '/usr/local/sbin/iocage')
        return binary

    def _iocage_admin(self, *args):
        try:
            return self._exec(self.iocage_admin_binary, *args)
        except socket.error as e:
            raise IocageError("Couldn't connect to instance [%s]:\n%s" % (self.instance.config_id, e))

    @lazy
    def iocage_admin_list_headers(self):
        rc, out, err = self._iocage_admin('list')
        if rc:
            raise IocageError(err.strip())
        lines = out.splitlines()
        if len(lines) < 2:
            raise IocageError("iocage list output too short:\n%s" % out.strip())
        headers = []
        current = ""
        for i, c in enumerate(lines[1]):
            if c != '-' or i >= len(lines[0]):
                headers.append(current.strip())
                if i >= len(lines[0]):
                    break
                current = ""
            else:
                current = current + lines[0][i]
        if headers != ['STA', 'JID', 'IP', 'Hostname', 'Root Directory']:
            raise IocageError("iocage list output has unknown headers:\n%s" % headers)
        return ('status', 'jid', 'ip', 'tag', 'root')

    def iocage_admin(self, command, **kwargs):
        # make sure there is no whitespace in the arguments
        for k, v in kwargs.items():
            if v is None:
                continue
            if command == 'console' and k == 'cmd':
                continue
            if len(v.split()) != 1:
                log.error("The value '%s' of kwarg '%s' contains whitespace", v, k)
                sys.exit(1)
        if command == 'console':
            return self._iocage_admin(
                'console',
                kwargs['tag'])
        elif command == 'create':
            args = [
                'create']
            jailtype = kwargs.get('jailtype')
            if jailtype is not None:
                args.extend([jailtype])
            args.extend([
                'tag='+kwargs['tag'],
                'ip4_addr="'+kwargs['ip']+'"'])
            rc, out, err = self._iocage_admin(*args)
            if rc:
                raise IocageError(err.strip())
        elif command == 'destroy':
            rc, out, err = self._iocage_admin(
                'destroy',
                '-f',
                kwargs['tag'])
            if rc:
                raise IocageError(err.strip())
        elif command == 'list':
            rc, out, err = self._iocage_admin('list')
            if rc:
                raise IocageError(err.strip())
            lines = out.splitlines()
            if len(lines) < 2:
                raise IocageError("iocage list output too short:\n%s" % out.strip())
            headers = self.iocage_admin_list_headers
            jails = {}
            for line in lines[2:]:
                line = line.strip()
                if not line:
                    continue
                entry = dict(zip(headers, line.split()))
                jails[entry.pop('tag')] = entry
            return jails
        elif command == 'start':
            rc, out, err = self._iocage_admin(
                'start',
                kwargs['tag'])
            if rc:
                raise IocageError(err.strip())
        elif command == 'stop':
            rc, out, err = self._iocage_admin(
                'stop',
                kwargs['tag'])
            if rc:
                raise IocageError(err.strip())
        else:
            raise ValueError("Unknown command '%s'" % command)


class MountsMassager(BaseMassager):
    def __call__(self, config, sectionname):
        value = BaseMassager.__call__(self, config, sectionname)
        mounts = []
        for line in value.splitlines():
            mount_options = line.split()
            if not len(mount_options):
                continue
            options = {}
            for mount_option in mount_options:
                if '=' not in mount_option:
                    raise ValueError("Mount option '%s' contains no equal sign." % mount_option)
                (key, value) = mount_option.split('=')
                (key, value) = (key.strip(), value.strip())
                if key == 'create':
                    value = value_asbool(value)
                    if value is None:
                        raise ValueError("Unknown value %s for option %s in %s of %s:%s." % (value, key, self.key, self.sectiongroupname, sectionname))
                if key == 'ro':
                    value = value_asbool(value)
                    if value is None:
                        raise ValueError("Unknown value %s for option %s in %s of %s:%s." % (value, key, self.key, self.sectiongroupname, sectionname))
                options[key] = value
            mounts.append(options)
        return tuple(mounts)


def get_common_massagers():
    from ploy.plain import get_massagers as plain_massagers
    return [(x.__class__, x.key) for x in plain_massagers()]


def get_instance_massagers(sectiongroupname='instance'):
    from ploy.config import BooleanMassager
    from ploy.config import StartupScriptMassager

    massagers = []

    for klass, tag in get_common_massagers():
        massagers.append(klass(sectiongroupname, tag))
    massagers.extend([
        MountsMassager(sectiongroupname, 'mounts'),
        BooleanMassager(sectiongroupname, 'no-terminate'),
        StartupScriptMassager(sectiongroupname, 'startup_script')])
    return massagers


def get_massagers():
    from ploy.config import BooleanMassager

    massagers = []

    sectiongroupname = 'ioc-instance'
    massagers.extend(get_instance_massagers(sectiongroupname))

    sectiongroupname = 'ioc-master'
    for klass, tag in get_common_massagers():
        massagers.append(klass(sectiongroupname, tag))
    massagers.extend([
        BooleanMassager(sectiongroupname, 'sudo'),
        BooleanMassager(sectiongroupname, 'debug-commands')])

    sectiongroupname = 'ioc-zfs'
    massagers.extend([
        BooleanMassager(sectiongroupname, 'create')])

    return massagers


def get_masters(ploy):
    masters = ploy.config.get('ioc-master', {})
    for master, master_config in masters.items():
        yield Master(ploy, master, master_config)


plugin = dict(
    get_massagers=get_massagers,
    get_masters=get_masters)
