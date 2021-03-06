Overview
========

The ploy_iocage plugin provides integration of `iocage`_ with `ploy`_ to manage `FreeBSD`_ jails.

.. _iocage: https://github.com/iocage/iocage
.. _ploy: https://github.com/ployground/
.. _FreeBSD: http://www.freebsd.org


Installation
============

ploy_iocage is best installed with easy_install, pip or with zc.recipe.egg in a buildout.


Masters
=======

To use ploy_iocage you need a host running FreeBSD on which you want to manage jails.

You declare a master with ``[ioc-master:masterid]`` where ``masterid`` is the name you want to use for this master.
Now you can either add options like for a ``plain`` ploy instance, or you can use the ``instance`` option to refer to another instance from your config like this::

    [ioc-master:master1]
    host = myhost.example.com

    [plain-instance:foohost]
    host = foohost.example.com

    [ioc-master:master2]
    instance = foohost

The latter is most useful in conjunction with other ploy backend plugins, as it allows you to easily switch between provisioners, i.e. to have an ``ioc-master`` provisioned on VirtualBox during development and on a ``plain`` instance in production.


Options
-------

``debug-commands``
  If set to ``yes``, the commands executed on the host are echoed locally.

``instance``
  The instance to use as host for this master.
  If empty, the local machine is used without an ssh connection.

``iocage``
  Path to the ``iocage`` script on the host.
  Defaults to ``/usr/local/sbin/iocage``.

``sudo``
  Use ``sudo`` to run commands on the host.


Instances
=========

At the moment all jails will be created using ZFS, so the host needs to be setup accordingly.


Options
-------

``ip``
  The ip address to use for the jail.
  **Required**

``jailtype``
  The **jailtype** to use for this jail. (-b, -c, -e) See the `iocage(8)` man pages for more info.

``iocage-tag``
  The **tag** to use for the jail. By default the id of the instance is used.

``mounts``
  Additional mount points for the jail.
  You can specify one mount point per line.
  The format is::

      src=SRC dst=DST [ro=true] [create=true]

  The ``src`` is the path on the host, ``dst`` is the path inside the jail.

  If ``ro`` is set to ``true``, then the mount is read only.

  When ``create`` is enabled, then the ``src`` path is created with ``mkdir -p``.
  The ``dst`` path is always created inside the jail with ``mkdir -p``.

  You can reference `ZFS sections`_ inside ``src`` with ``{zfs[name]}`` where ``name`` is the ``ioc-zfs`` section name.
  You can use the name of the jail instance with ``{name}}`` in both ``src`` and ``dst``.
  Examples::

      src=/foo dst=/foo
      src={zfs[backup]} dst=/bak
      src={zfs[data]}/{name} dst=/mnt/data create=true
      src={zfs[static]} dst=/mnt/static ro=true

``no-terminate``
  If set to ``yes``, the jail can't be terminated via ploy until the setting is changed to ``no`` or removed entirely.

``startup_script``
  Path to a local script (relative to the location of the configuration file) which will be run inside the jail right after creation and first start of the jail.


ZFS sections
============

You can specify ZFS filesystems via ``[ioc-zfs:name]`` sections.
This is used in mounts of jails to get the mountpoint and verify that the path exists and is it's own ZFS filesystem.
You can also create new ZFS filesystems with the ``create`` option.


Options
-------

``create``
  If set to ``yes``, the filesystem is created when first used.

``path``
  Specifies the path of this filesystem.
  This is not the mountpoint, but the ZFS path.
  You can reference other ZFS sections with ``{zfs[name][path]}``.
  The ``name`` is the name of the referenced ZFS section.
  The ``[path]`` at the end is mandatory, as otherwise you would get the mountpoint of the referenced ZFS section.
  Examples::

    [ioc-zfs:data]
    path = tank/data

    [ioc-zfs:shared]
    path = {zfs[data][path]}/shared

    [ioc-zfs:jails]
    path = {zfs[data][path]}/jails

    [ioc-zfs:backup]
    create = true
    path = tank/backup
