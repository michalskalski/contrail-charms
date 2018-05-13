import functools
import os
import shutil
from socket import gethostname
from subprocess import (
    check_call,
    check_output,
)
from time import sleep, time
import yaml

import apt_pkg
import json

import netaddr
import netifaces
import ConfigParser

from charmhelpers.core import sysctl
from charmhelpers.core.hookenv import (
    config,
    log,
    related_units,
    relation_get,
    relation_ids,
    status_set,
    WARNING,
)

from charmhelpers.core.host import (
    file_hash,
    restart_on_change,
    write_file,
    service_restart,
    init_is_systemd,
    get_total_ram,
    mkdir,
    lsb_release,
)

from charmhelpers.core.templating import render

apt_pkg.init()
config = config()

HEATCONFPATH = '/etc/heat/heat.conf'
#Location of contrail heat resources provided by python-contrail
CONTRAIL_RESOURCE_PATH = '/usr/lib/python2.7/dist-packages/vnc_api/gen/heat/resources'


def get_controller_address():
    ip = config.get("api_ip")
    port = config.get("api_port")
    api_vip = config.get("api_vip")
    if api_vip:
        ip = api_vip
    return (ip, port) if ip and port else (None, None)


def _load_json_from_config(key):
    value = config.get(key)
    return json.loads(value) if value else {}


def configure_heat(heat_plugin_path):
    """Configure heat to use contrail's heat resources
    
    This function is a hack due the lack of appropriate interface in
    heat charm for adding configuration outside DEFAULT section.
    """

    try:
        os.makedirs(heat_plugin_path)
    except OSError:
        if not os.path.isdir(heat_plugin_path):
            raise

    if not os.path.isdir('{}/contrail'.format(heat_plugin_path)):
        shutil.copytree(CONTRAIL_RESOURCE_PATH, '{}/contrail'.format(heat_plugin_path))

    identity = _load_json_from_config("auth_info")
    api_ip, api_port = get_controller_address()

    parser = ConfigParser.ConfigParser()
    parser.read(HEATCONFPATH)

    try:
        plugin_dirs = parser.get('DEFAULT', 'plugin_dirs')
    except ConfigParser.NoOptionError:
        parser.set('DEFAULT', 'plugin_dirs', heat_plugin_path)
    else:
        paths = { p.strip() for p in plugin_dirs.split(',') }
        paths.add(heat_plugin_path)
        parser.set('DEFAULT', 'plugin_dirs', ','.join(paths))

    if not parser.has_section('clients_contrail'):
        parser.add_section('clients_contrail')

    parser.set('clients_contrail', 'user', identity.get("keystone_admin_user"))
    parser.set('clients_contrail', 'password', identity.get("keystone_admin_password"))
    parser.set('clients_contrail', 'tenant', identity.get("keystone_admin_tenant"))
    parser.set('clients_contrail', 'api_server', api_ip)
    parser.set('clients_contrail', 'auth_host_ip', identity.get("keystone_ip"))

    with open(HEATCONFPATH, 'wb') as heatconf:
        parser.write(heatconf) 

    service_restart('heat-engine')

    status_set("active", "Contrail resources installed")
