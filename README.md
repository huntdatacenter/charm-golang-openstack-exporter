# Golang Openstack Exporter Charm

![GitHub Action CI badge](https://github.com/huntdatacenter/charm-golang-openstack-exporter/workflows/ci/badge.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Usage

This charm requires relation to keystone or configuration of identity credentials, to be able to source metrics about openstack services:

```
juju deploy cs:~huntdatacenter/golang-openstack-exporter openstack-exporter
```

```
juju add-relation openstack-exporter keystone
juju add-relation openstack-exporter prometheus
```

## Development

Here are some helpful commands to get started with development and testing:

```
$ make help
lint                  Run linter
build                 Build charm
deploy                Deploy charm
upgrade               Upgrade charm
force-upgrade         Force upgrade charm
deploy-xenial-bundle  Deploy Xenial test bundle
deploy-bionic-bundle  Deploy Bionic test bundle
test-bundle           Test deployed bundle with jujuna
push                  Push charm to stable channel
clean                 Clean .tox and build
help                  Show this help
```

## Links

- https://github.com/openstack-exporter/openstack-exporter


# Overview

This charm provides openstack-exporter for prometheus

# Build
The fully built charm needs the following source branch
* https://git.launchpad.net/prometheus-openstack-exporter-charm

## To build the charm, do:

Prepare the environment

    mkdir -p layers charms/xenial
    export JUJU_REPOSITORY=$PWD/charms

Clone the repositories

    pushd layers
    git clone https://git.launchpad.net/prometheus-openstack-exporter-charm
    popd

Build the charm, and symlink for juju-1 compatibility

    charm build layers/charm-prometheus-openstack-exporter
    ln -s ../builds/prometheus-openstack-exporter charms/xenial

# Usage

With the OpenStack nova-compute and neutron-gateway charms:

    juju deploy local:xenial/prometheus-openstack-exporter

This charm supports relating to keystone, but keystone-credentials
interface seems to be flaky, and hard to remove-relation, so the
charm works around this by adding 'os-credentials' setting as a YAML
dict (see setting description hints)

    juju config prometheus-openstack-exporter os-credentials="{ ... }"

    juju add-relation prometheus-openstack-exporter swift-storage-z1
    juju add-relation prometheus-openstack-exporter swift-storage-z2
    juju add-relation prometheus-openstack-exporter swift-storage-z3
    juju add-relation prometheus prometheus-openstack-exporter
