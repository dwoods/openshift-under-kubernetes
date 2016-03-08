# Openshift under Kubernetes

Deploys OpenShift Origin to an existing Kubernetes cluster. This is arguably the most ideal way to deploy OpenShift, for a few reasons:

 - OpenShift Origin is a layer on top of Kubernetes
 - Kubernetes is extremely flexible in the ways it can be deployed, with kube-up
 - Allowing Kubernetes and OpenShift to be independently updated is ideal for feature iteration and stability
 - This is much easier than dealing with Ansible under OpenShift v3.

# Deploy Process

This is the process this system uses to deploy OpenShift:

- Fetch the ServiceAccount public key by creating a temporary namespace and pod and fetching via the pod logs.
- Configure using a configuration template.
- Give the user the option to edit this template before submitting it.
- Create the `openshift-origin` namespace.
- Create a `PersistentVolume` inside this namespace for storage of OpenShift data.
- Create a `PersistentVolumeClaim` to claim this storage.
- Create the etcd single-node cluster for the OpenShift system.
- Setup the OpenShift service. Configure the system to use the internal cluster IP for internal communications and a LoadBalancer IP for the public communications.
- Create the OpenShift replication controller.
- Output some information about how to use oadm and whatnot.

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
