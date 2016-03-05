# Openshift under Kubernetes

Deploys OpenShift Origin to an existing Kubernetes cluster.




# Usage

To use it:

    $ openshift-under-kubernetes --help

This will output:

    Usage: openshift-under-kubernetes [OPTIONS] COMMAND [ARGS]...

    Options:
      --config PATH             kube config path
      --context TEXT            kube context
      --openshift-version TEXT  force openshift version (default 1.1.3)
      --secure / --no-secure    enables https checking
      -y                        auto-confirm yes for all questions
      --help                    Show this message and exit.

    Commands:
      deploy  Deploy OpenShift to the cluster.
      info    Show cluster information

You can get started by running `openshift-under-kubernetes info` to check that the tool can properly communicate with your cluster.
