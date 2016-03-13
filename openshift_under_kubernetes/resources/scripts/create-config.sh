#!/bin/sh
set -e
mkdir -p /config_out
/usr/bin/openshift start master --write-config=/config_out --kubeconfig=/etc/kube_config/kubeconfig --master="$OPENSHIFT_INTERNAL_ADDRESS" --public-master="$OPENSHIFT_EXTERNAL_ADDRESS" --etcd="$ETCD_ADDRESS"  2>&1 > /dev/null
cd /config_out && tar --to-stdout --gzip --create ./* 2>/dev/null | base64 -w0 | cat
