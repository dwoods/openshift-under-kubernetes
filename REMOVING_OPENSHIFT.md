Removing Openshift
==================

Removing OpenShift Origin from a cluster can cause some issues.

Any "project" created in OpenShift creates a corresponding namespace in Kubernetes. The created namespaces use the openshift finalizer, rather than the Kubernetes one. This means that attempts to delete the Kubernetes namespace without OpenShift running will result in the namespaces staying in the "Terminated" state forever.

Eventually I plan to address this automatically with this tool (adding a CLI command to delete a namespace with an openshift finalizer). For now, you can follow the instructions in [this kubernetes issue](https://github.com/kubernetes/kubernetes/issues/19317).
