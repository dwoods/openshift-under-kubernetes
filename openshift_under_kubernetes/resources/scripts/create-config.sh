#!/bin/sh
set -e

# Meant to be run inside the container!!! DO NOT run this yourself.
mkdir -p /config_out
/usr/bin/openshift start master --write-config=/config_out --kubeconfig=/etc/kube_config/kubeconfig --master="$OPENSHIFT_INTERNAL_ADDRESS" --public-master="$OPENSHIFT_EXTERNAL_ADDRESS" --etcd="$ETCD_ADDRESS" > /dev/null
# Zip the config_out contents and dump to stdout
tar --to-stdout --gzip --create /config_out/*
