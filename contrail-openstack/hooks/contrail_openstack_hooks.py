#!/usr/bin/env python

import json
import shutil
from subprocess import CalledProcessError, check_output
import sys
import uuid
import yaml

from charmhelpers.core.hookenv import (
    Hooks,
    UnregisteredHookError,
    config,
    log,
    related_units,
    relation_get,
    relation_ids,
    relation_set,
    status_set,
    leader_get,
    leader_set,
    is_leader,
    unit_private_ip,
    application_version_set,
)

from charmhelpers.core.host import (
    service_restart,
)

from charmhelpers.fetch import (
    apt_install,
    apt_update,
    apt_upgrade,
    configure_sources
)

from contrail_openstack_utils import (
    write_configs,
    update_service_ips,
    get_context
)

NEUTRON_API_PACKAGES = ["neutron-plugin-contrail"]


hooks = Hooks()
config = config()


@hooks.hook("install.real")
def install():
    status_set('maintenance', 'Installing...')
    configure_sources(True, "install-sources", "install-keys")
    apt_update(fatal=True)
    apt_upgrade(fatal=True, dist=False)
    status_set("blocked", "Missing relation to contrail-controller")


@hooks.hook("config-changed")
def config_changed():
    if config.changed("install-sources") or config.changed("install-keys"):
        configure_sources(True, "install-sources", "install-keys")
        apt_update(fatal=True)
        apt_upgrade(fatal=True, dist=False)
        _notify_neutron()

    if is_leader():
        res = _configure_metadata_shared_secret()
        if res:
            _notify_controller()


@hooks.hook("leader-elected")
def leader_elected():
    res = _configure_metadata_shared_secret()
    if res:
        _notify_controller()
    _notify_nova()


@hooks.hook("leader-settings-changed")
def leader_settings_changed():
    _notify_nova()


@hooks.hook("contrail-controller-relation-joined")
def contrail_controller_joined():
    if not is_leader():
        return

    data = _get_orchestrator_info()
    relation_set(**data)


@hooks.hook("contrail-controller-relation-changed")
def contrail_controller_changed():
    data = relation_get()

    def _update_config(key, data_key):
        if data_key in data:
            val = data[data_key]
            if val is not None:
                config[key] = val
            else:
                config.pop(key, None)

    _update_config("auth_info", "auth-info")
    _update_config("api_vip", "api-vip")
    _update_config("api_ip", "private-address")
    _update_config("api_port", "port")
    _update_config("auth_mode", "auth-mode")

    info = data.get("agents-info")
    if not info:
        config["dpdk"] = False
        log("DPDK for current host is False. agents-info is not provided.")
    else:
        ip = unit_private_ip()
        value = json.loads(info).get(ip, False)
        if not isinstance(value, bool):
            value = yaml.load(value)
        config["dpdk"] = value
        log("DPDK for host {ip} is {dpdk}".format(ip=ip, dpdk=value))

    config.save()
    write_configs()

    # apply information to base charms
    _notify_nova()
    _notify_neutron()
    _notify_heat()

    status_set("active", "Unit is ready")

    # auth_info can affect endpoints
    if is_leader():
        changed = update_service_ips()
        if changed:
            _notify_controller()


@hooks.hook("contrail-controller-relation-departed")
def contrail_cotroller_departed():
    units = [unit for rid in relation_ids("contrail-controller")
                  for unit in related_units(rid)]
    if units:
        return

    keys = ["auth_info", "api_vip", "api_ip", "api_port", "auth_mode"]
    for key in keys:
        config.pop(key, None)
    config.save()
    write_configs()
    status_set("blocked", "Missing relation to contrail-controller")


def _configure_metadata_shared_secret():
    secret = leader_get("metadata-shared-secret")
    if config["enable-metadata-server"] and not secret:
        secret = str(uuid.uuid4())
    elif not config["enable-metadata-server"] and secret:
        secret = None
    else:
        return False

    leader_set(settings={"metadata-shared-secret": secret})
    return True


def _notify_controller():
    data = _get_orchestrator_info()
    for rid in relation_ids("contrail-controller"):
        if related_units(rid):
            relation_set(relation_id=rid, **data)


def _notify_nova():
    for rid in relation_ids("nova-compute"):
        if related_units(rid):
            nova_compute_joined(rid)


def _notify_neutron():
    for rid in relation_ids("neutron-api"):
        if related_units(rid):
            neutron_api_joined(rid)

def _notify_heat():
    for rid in relation_ids("heat-plugin"):
        if related_units(rid):
            heat_plugin_joined(rid)


def _get_orchestrator_info():
    info = {"cloud_orchestrator": "openstack", "unit-type": "openstack"}

    if config["enable-metadata-server"]:
        info["metadata_shared_secret"] = leader_get("metadata-shared-secret")

    def _add_to_info(key):
        value = leader_get(key)
        if value:
            info[key] = value

    _add_to_info("compute_service_ip")
    _add_to_info("image_service_ip")
    _add_to_info("network_service_ip")
    return {"orchestrator-info": json.dumps(info)}

@hooks.hook("heat-plugin-relation-joined")
def heat_plugin_joined(rel_id=None):
    configure_sources(True, "install-sources", "install-keys")
    apt_upgrade(fatal=True, dist=True)
    apt_install(["contrail-heat"], fatal=True)

    try:
        output = check_output(["dpkg-query", "-f", "${Version}\\n",
                               "-W", "contrail-heat"])
        version = output.decode('UTF-8').rstrip()
        application_version_set(version)
    except CalledProcessError:
        return None
    plugin_dirs = config.get("heat-plugin-dirs")
    ctx = get_context()
    sections = {
        "clients_contrail": [
            ("user", ctx.get("keystone_admin_user")),
            ("password", ctx.get("keystone_admin_password")),
            ("tenant", ctx.get("keystone_admin_tenant")),
            ("api_server", ctx.get("api_server")),
            ("auth_host_ip", ctx.get("keystone_ip"))
        ]
    }

    conf = {
        "heat": {
            "/etc/heat/heat.conf": {
              "sections": sections
            }
         }
    }
    settings = {
        "plugin-dirs": plugin_dirs,
        "subordinate_configuration": json.dumps(conf)
    }
    relation_set(relation_id=rel_id, relation_settings=settings)

@hooks.hook("neutron-api-relation-joined")
def neutron_api_joined(rel_id=None):
    apt_install(NEUTRON_API_PACKAGES, fatal=True)
    try:
        cmd = ["dpkg-query", "-f", "${Version}\\n",
               "-W", "neutron-plugin-contrail"]
        version = check_output(cmd).decode("UTF-8").rstrip()
        application_version_set(version)
        # save version for future using
        version = version.split('-')[0].split('.')
        m = int(version[0])
        r = int(version[1]) if len(version) > 1 else 0
        a = int(version[2]) if len(version) > 2 else 0
        config["version"] = (m * 1e4) + (r * 1e2) + a
        config.save()
    except CalledProcessError as e:
        log("Couldn't detect installed application version: " + str(e))

    # create plugin config
    base = "neutron_plugin_contrail.plugins.opencontrail"
    plugin = base + ".contrail_plugin.NeutronPluginContrailCoreV2"
    service_plugins = base + ".loadbalancer.v2.plugin.LoadBalancerPluginV2"
    extensions = [
        "/usr/lib/python2.7/dist-packages/neutron_plugin_contrail/extensions",
        "/usr/lib/python2.7/dist-packages/neutron_lbaas/extensions"]
    conf = {
      "neutron-api": {
        "/etc/neutron/neutron.conf": {
          "sections": {
            "DEFAULT": [
              ("api_extensions_path", ":".join(extensions))
            ]
          }
        }
      }
    }
    settings = {
        "neutron-plugin": "contrail",
        "core-plugin": plugin,
        "neutron-plugin-config":
            "/etc/neutron/plugins/opencontrail/ContrailPlugin.ini",
        "service-plugins": service_plugins,
        "quota-driver": base + ".quota.driver.QuotaDriver",
        "subordinate_configuration": json.dumps(conf),
    }
    auth_mode = config.get("auth_mode", "cloud-admin")
    if auth_mode == "rbac":
        settings["extra_middleware"] = [{
            "name": "user_token",
            "type": "filter",
            "config": {
                "paste.filter_factory":
                    base + ".neutron_middleware:token_factory"
            }
        }]
    relation_set(relation_id=rel_id, relation_settings=settings)

    # if this hook raised after contrail-controller we need
    # to overwrite default config file after installation
    write_configs()


@hooks.hook("nova-compute-relation-joined")
def nova_compute_joined(rel_id=None):
    if config.get("dpdk", False):
        # contrail nova packages contain vrouter vhostuser vif
        shutil.copy("files/40contrail", "/etc/apt/preferences.d")
        apt_install(["nova-compute", "libvirt-bin", "contrail-nova-vif"],
                    options=["--reinstall", "--force-yes", "-fy",
                             "-o", "Dpkg::Options::=--force-confnew"],
                    fatal=True)
        service_restart("nova-api-metadata")

    # create plugin config
    sections = {
        "DEFAULT": [
            ("firewall_driver", "nova.virt.firewall.NoopFirewallDriver")
        ]
    }
    if config.get("dpdk", False):
        sections["CONTRAIL"] = [("use_userspace_vhost", "True")]
        sections["libvirt"] = [("use_huge_pages", "True")]
    conf = {
      "nova-compute": {
        "/etc/nova/nova.conf": {
          "sections": sections
        }
      }
    }
    settings = {
        "metadata-shared-secret": leader_get("metadata-shared-secret"),
        "subordinate_configuration": json.dumps(conf)}
    relation_set(relation_id=rel_id, relation_settings=settings)


@hooks.hook("update-status")
def update_status():
    if not is_leader():
        return
    changed = update_service_ips()
    if changed:
        _notify_controller()


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log("Unknown hook {} - skipping.".format(e))


if __name__ == "__main__":
    main()
