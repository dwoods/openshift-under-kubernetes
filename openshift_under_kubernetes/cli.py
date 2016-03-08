import os
import click
import tempfile
import yaml
import base64
import tarfile
import time
import shutil

from pkg_resources import resource_string, resource_listdir
from subprocess import call
from .os_kube import OpenshiftKubeDeployer
from .more_objects import PersistentVolume
from pykube.objects import Pod

EDITOR = os.environ.get('EDITOR','vim')

@click.group()
@click.option("--config", default="~/.kube/config", help="kube config path", envvar="KUBE_CONFIG", type=click.Path())
@click.option("--context", default=None, help="kube context", envvar="KUBE_CONTEXT")
@click.option("--openshift-version", default="1.1.3", help="force openshift version (default 1.1.3)")
@click.option("--secure/--no-secure", default=False, help="enables https checking", envvar="KUBE_SESSION_SECURE")
@click.option("-y", help="auto-answer for all confirmations", is_flag=True)
@click.pass_context
def cli(ctx, config, context, secure, openshift_version, y):
    ctx.auto_confirm = y
    if ctx.auto_confirm:
        print("[warn] -y is set, will automatically confirm and proceed with actions.")

    ctx.kube_deployer = OpenshiftKubeDeployer(os.path.expanduser(config), context, secure)
    ctx.obj = ctx.kube_deployer
    ctx.obj.temp_dir = tempfile.mkdtemp()
    ctx.obj.auto_confirm = y
    ctx.obj.os_version = openshift_version
    if ctx.obj.os_version.find("v") != 0:
        ctx.obj.os_version = "v" + ctx.obj.os_version
    ctx.obj.scripts_resource = __name__.split('.')[0] + ".resources.scripts"

@cli.command()
@click.pass_obj
def info(ctx):
    """Show cluster information"""
    ctx.init_with_checks()

@cli.command()
@click.option("--persistent-volume", default="openshift-etcd1", prompt=True, help="Name of existing PersistentVolume of at least 2Gi size for storage")
@click.option("--public-hostname", default=None, help="public hostname that will be DNSd to the public IP", envvar="OPENSHIFT_PUBLIC_DNS")
@click.option("--load-balancer/--no-load-balancer", default=True, help="use load balancer, otherwise node port", envvar="OPENSHIFT_CREATE_LOADBALANCER")
@click.pass_obj
def deploy(ctx, persistent_volume, load_balancer, public_hostname):
    """Deploy OpenShift to the cluster."""
    if not ctx.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)

    if ctx.consider_openshift_deployed:
        print("I think OpenShift is already deployed. Use undeploy first to remove it before installing it again.")
        print("Consider if you really need a full redeploy. You can update without re-deploying!")
        exit(1)

    print()
    if "openshift" in ctx.namespace_names or "openshift-origin" in ctx.namespace_names:
        print("The namespaces 'openshift' and/or 'openshift-origin' exist, this indicates a potentially existing/broken install.")
        if ctx.auto_confirm:
            print("Auto confirm (-y) option set, clearing existing installation.")
        else:
            print("Really consider the decision you're about to make.")
            if not click.confirm("Do you want to clear the existing installation?"):
                print("Okay, cancelling.")
                exit(1)

        # Handle oddities with finalizers?
        # todo: delete "openshift" and "openshift-infra" namespaces
        ctx.delete_namespace_byname("openshift-origin")
        time.sleep(1)

    print("Preparing to execute deploy...")
    print("Deploy temp dir: " + ctx.temp_dir)

    # Setup the deploy state namespace
    ctx.cleanup_osdeploy_namespace()
    ctx.create_osdeploy_namespace()

    # Check the persistentvolume exists
    if ctx.find_persistentvolume(persistent_volume) == None:
        print(" [!] persistentvolume with name " + persistent_volume + " does not exist. Did you create it?")
        exit(1)

    # Grab the service account key
    servicekey_pod = ctx.create_servicekey_pod()

    # Get the key
    ctx.service_cert = ctx.observe_servicekey_pod(servicekey_pod)

    # Kill the pod
    servicekey_pod.delete()

    # Save the key temporarily
    with open(ctx.temp_dir + "/serviceaccounts.public.key", 'w') as f:
        f.write(ctx.service_cert)

    # Create the namespaces
    ctx.create_namespace("openshift-origin")

    # Create the service
    if load_balancer:
        print("Will use load balancer type service.")
    else:
        print("Will use node port type service.")
    os_service = ctx.create_os_service(load_balancer)

    # Wait for it to be ready if it's a load balancer
    if load_balancer:
        print("Waiting for service load balancer IP to be allocated...")
        ctx.wait_for_loadbalancer(os_service)
    else:
        os_service.reload()

    tmp = os_service.obj["status"]["loadBalancer"]["ingress"][0]
    external_os_ip = None
    external_is_hostname = False
    if "hostname" in tmp:
        external_os_ip = tmp["hostname"]
        external_is_hostname = True
    else:
        external_os_ip = tmp["ip"]
    internal_os_ip = os_service.obj["spec"]["clusterIP"]
    ctx.os_internal_ip = internal_os_ip
    ctx.os_external_ip = external_os_ip

    print("External OpenShift IP: " + external_os_ip)
    print("Internal OpenShift IP: " + internal_os_ip)

    if public_hostname != None:
        print("You need to DNS map like this:")
        if external_is_hostname:
            print(public_hostname + ".\t300\tIN\tCNAME\t" + external_os_ip)
        else:
            print(public_hostname + ".\t300\tIN\tA\t" + external_os_ip)
        ctx.os_external_ip = public_hostname

    # Create a 'secret' containing the script to run to config.
    create_config_script = (resource_string(ctx.scripts_resource, 'create-config.sh'))

    # Build the secret
    create_config_secret_kv = {"create-config.sh": create_config_script}
    create_config_secret = ctx.build_secret("create-config-script", "openshift-deploy", create_config_secret_kv)
    create_config_secret.create()

    # Build the kubeconfig secret
    kubeconfig_secret_kv = {"kubeconfig": yaml.dump(ctx.config.doc).encode('ascii')}
    kubeconfig_secret = ctx.build_secret("kubeconfig", "openshift-deploy", kubeconfig_secret_kv)
    kubeconfig_secret.create()

    # Generate the openshift config by running a temporary pod on the cluster
    print("Generating openshift config via cluster...")
    conf_pod = ctx.build_config_pod(ctx.os_version)
    conf_pod.create()
    with open(ctx.temp_dir + "/config_bundle.tar.gz", 'wb') as f:
        conf_bundle = ctx.observe_config_pod(conf_pod)
        conf_bundle_data = base64.b64decode(conf_bundle)
        f.write(conf_bundle_data)
    conf_pod.delete()

    # Extract
    tar = tarfile.open(ctx.temp_dir + "/config_bundle.tar.gz")
    tar.extractall(ctx.temp_dir + "/config/")
    tar.close()

    # Move kubeconfig in
    with open(ctx.temp_dir + "/config/external-master.kubeconfig", 'w') as f:
        f.write(yaml.dump(ctx.config.doc))

    # Delete tarfile
    os.remove(ctx.temp_dir + "/config_bundle.tar.gz")

    # Do some processing on the master-config yaml
    conf = None
    with open(ctx.temp_dir + '/config/master-config.yaml') as f:
        conf = f.read()
    conf = ctx.fix_master_config(conf)

    # Write the serviceaccounts file again
    with open(ctx.temp_dir + "/config/serviceaccounts.public.key", 'w') as f:
        f.write(ctx.service_cert)

    # Write the fixed master config
    with open(ctx.temp_dir + "/config/master-config.yaml", 'w') as f:
        f.write(conf)

    # Allow the user to edit the openshift config last second
    print("Generated updated master-config.yaml.")
    if ctx.auto_confirm:
        print("Auto confirm (-y) option set, skipping master-config.yaml edit opportunity.")
    else:
        if click.confirm("Do you want to edit master-config.yaml?"):
            call([EDITOR, ctx.temp_dir + "/config/master-config.yaml"])

    # Cleanup a bit
    kubeconfig_secret.delete()
    create_config_secret.delete()

    # Serialize the config to a secret
    openshift_config_kv = {}
    for filen in os.listdir(ctx.temp_dir + "/config"):
        with open(ctx.temp_dir + "/config/" + filen, 'rb') as f:
            openshift_config_kv[filen] = f.read()
    openshift_config_secret = ctx.build_secret("openshift-config", "openshift-origin", openshift_config_kv)

    # Save the secret
    openshift_config_secret.create()

    # Starting etcd setup... build PersistentVolumeClaim
    etcd_pvc = ctx.build_pvc("openshift-etcd1", "openshift-origin", "2Gi")
    etcd_pvc.create()

    # Create the etcd controller
    etcd_rc = ctx.build_etcd_rc("openshift-etcd1")
    etcd_svc = ctx.build_etcd_service()

    print("Creating etcd service...")
    etcd_svc.create()

    print("Creating etcd controller...")
    etcd_rc.create()

    print("Waiting for etcd pod to be created...")
    etcd_pod = None
    # Wait for the pod to exist
    while etcd_pod == None:
        etcd_pods = Pod.objects(ctx.api).filter(selector={"app": "etcd"}, namespace="openshift-origin").response["items"]
        if len(etcd_pods) < 1:
            time.sleep(0.5)
            continue
        etcd_pod = Pod(ctx.api, etcd_pods[0])

    # Wait for it to run
    ctx.wait_for_pod_running(etcd_pod)

    # Create the controller config
    print("Creating openshift replication controller...")
    openshift_rc = ctx.build_openshift_rc(ctx.os_version)
    openshift_rc.create()

    print("Waiting for openshift pod to be created...")
    openshift_pod = None
    # Wait for the pod to exist
    while openshift_pod == None:
        pods = Pod.objects(ctx.api).filter(namespace="openshift-origin", selector={"app": "openshift"}).response["items"]
        if len(pods) < 1:
            time.sleep(0.5)
            continue
        openshift_pod = Pod(ctx.api, pods[0])

    # Wait for it to run
    ctx.wait_for_pod_running(openshift_pod)

    print()
    print(" == OpenShift Deployed ==")
    print("External IP: " + ctx.os_external_ip)

    ctx.cleanup_osdeploy_namespace()
    shutil.rmtree(ctx.temp_dir)
    pass

@cli.command()
@click.pass_obj
def undeploy(ctx):
    """Removes OpenShift from the cluster."""
    print("Preparing to remove OpenShift...")
    if ctx.auto_confirm:
        print("Auto confirm (-y) option set, clearing existing installation.")
    else:
        print("Really consider the decision you're about to make.")
        if not click.confirm("Do you want to clear the existing installation?"):
            print("Okay, cancelling.")
            exit(1)
    ctx.delete_namespace_byname("openshift-origin")
    print("Note: namespaces with openshift finalizer will need to be manually deleted if desired.")
    print("See: https://github.com/paralin/openshift-under-kubernetes/blob/master/REMOVING_OPENSHIFT.md")

@cli.command()
@click.pass_obj
def getconfig(ctx):
    """Downloads the entire openshift config to a local directory."""

@cli.command()
@click.option("--config-output-dir", default=".", help="directory to write the openshift config", envvar="KUBE_CONFIG_OUTPUT_DIR", type=click.Path(exists=True), prompt=True)
@click.pass_obj
def getconfig(ctx, config_output_dir):
    config_dir = config_output_dir
    """Writes the entire openshift config to a directory for inspection."""
    if not ctx.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)

    if not ctx.consider_openshift_deployed:
        print("I think OpenShift is not yet deployed. Use deploy first to create it.")
        exit(1)
    config_secret = ctx.build_secret("openshift-config", "openshift-origin", {})
    config_secret.reload()
    config_secret_kv = config_secret.obj["data"]
    print("Got config with " + str(len(config_secret_kv)) + " files.")

    # deserialize base64
    for k in config_secret_kv:
        config_secret_kv[k] = base64.b64decode(config_secret_kv[k]).decode('ascii')

    # write files
    for k in config_secret_kv:
        print("Writing " + k + " to " + config_dir)
        with open(config_dir + "/" + k, 'w') as f:
            f.write(config_secret_kv[k])

    print("Done fetching config.")

def main():
    cli()
