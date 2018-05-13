#!/usr/bin/env python

import json
import os
import shutil
from socket import gethostname, gethostbyname
import sys

from charmhelpers.core.hookenv import (
    Hooks,
    config,
    status_set,
    application_version_set,
    UnregisteredHookError,
    relation_get,
    log
)

from charmhelpers.fetch import (
    apt_install,
    apt_upgrade,
    configure_sources
)

from subprocess import (
    CalledProcessError, 
    check_output
)

from contrail_heat_utils import (
    configure_heat
)

hooks = Hooks()
config = config()

PACKAGES=['contrail-heat']

@hooks.hook("install.real")
def install():

    status_set("maintenance", "Installing...")

    configure_sources(True, "install-sources", "install-keys")
    apt_upgrade(fatal=True, dist=True)
    apt_install(PACKAGES, fatal=True)

    try:
        output = check_output(["dpkg-query", "-f", "${Version}\\n",
                               "-W", "contrail-heat"])
        version = output.decode('UTF-8').rstrip()
        application_version_set(version)
    except CalledProcessError:
        return None

@hooks.hook("contrail-controller-relation-changed")
def contrail_controller_changed():
    data = relation_get()
    log("RelData: " + str(data))

    def _update_config(key, data_key):
        if data_key in data:
            config[key] = data[data_key]

    _update_config("api_ip", "private-address")
    _update_config("api_port", "port")
    _update_config("api_vip", "api-vip")
    _update_config("auth_info", "auth-info")
    _update_config("orchestrator_info", "orchestrator-info")
    config.save()

    configure_heat(config['heat-plugin-path'])

@hooks.hook("contrail-controller-relation-departed")
def contrail_controller_node_departed():
    units = [unit for rid in relation_ids("contrail-controller")
                      for unit in related_units(rid)]
    if units:
        return

    status_set("blocked", "Missing relation to contrail-controller")

def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log("Unknown hook {} - skipping.".format(e))


if __name__ == "__main__":
    main()
