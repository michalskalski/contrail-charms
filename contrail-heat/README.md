Overview
--------

OpenContrail (www.opencontrail.org) is a fully featured Software Defined
Networking (SDN) solution for private clouds. It supports high performance
isolated tenant networks without requiring external hardware support. It
provides a Neutron plugin to integrate with OpenStack.

This subordinate charm install contrail-heat package which provides 
contrail's heat resources and configure heat to use them.

Only OpenStack Mitaka or newer is supported.
Only for Contrail 4.0 for now.
Juju 2.0 is required.

Usage
-----

Contrail Controller are prerequisite service to deploy.

Once ready, deploy and relate as follows:

    juju deploy contrail-heat
    juju add-relation contrail-heat:juju-info heat:juju-info
    juju add-relation contrail-heat contrail-controller

Install Sources
---------------

The version of packages installed when deploying must be configured using the
'install-sources' option. This is a multilined value that may refer to PPAs or
Deb repositories.

Control Node Relation
---------------------

This charm is typically related to contrail-controller.
This instructs the heat to use the API endpoints for
locating needed information.
