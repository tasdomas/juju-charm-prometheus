# Juju prometheus charm

This charm provides the Prometheus monitoring system from
http://prometheus.io/.

## How to deploy the charm

## Development

Explicitly set `JUJU_REPOSITORY`:

    export JUJU_REPOSITORY=/path/to/charms
    mkdir -p $JUJU_REPOSITORY/layers

Branch code to:

    $JUJU_REPOSITORY/layers/prometheus/

Modify

Assemble the charm:

    charm build

# How to deploy with storge (requires juju 1.25)

    juju deploy local:trusty/prometheus --storage metrics-filesystem=rootfs prometheus
