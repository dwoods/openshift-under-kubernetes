import os
import click
import tempfile
import yaml
from pkg_resources import resource_string, resource_listdir

from .os_kube import OpenshiftKubeDeployer

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
    if ctx.obj.os_version.find("v") == 0:
        ctx.obj.os_version = ctx.obj.os_version[1:]
    ctx.obj.scripts_resource = __name__.split('.')[0] + ".resources.scripts"

@cli.command()
@click.pass_obj
def info(ctx):
    """Show cluster information"""
    ctx.init_with_checks()

@cli.command()
@click.option("--persistent-volume", default=None, help="Path to persistent volume yaml", type=click.Path(exists=True))
@click.option("--openshift-config", default=None, help="Path to OpenShift config yaml", type=click.Path(exists=True))
@click.option("--public-hostname", default=None, help="public hostname that will be DNSd to the public IP", envvar="OPENSHIFT_PUBLIC_DNS")
@click.option("--load-balancer/--no-load-balancer", default=True, help="use load balancer, otherwise node port", envvar="OPENSHIFT_CREATE_LOADBALANCER")
@click.pass_obj
def deploy(ctx, persistent_volume, openshift_config, load_balancer, public_hostname):
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
        ctx.delete_namespace_byname("openshift")
        ctx.delete_namespace_byname("openshift-origin")

    # Preliminary questions
    if openshift_config != None:
        print("Using specified config path as openshift config overrides.")
    else:
        print("No openshift config specified, will use defaults.")

    if persistent_volume == None:
        print("Persistent volume file not specified, will use HostDir on default.")
    else:
        print("Will use PersistentVolume as per spec in specified path.")

    print("Preparing to execute deploy...")
    print("Deploy temp dir: " + ctx.temp_dir)

    # Setup the deploy state namespace
    ctx.cleanup_osdeploy_namespace()
    ctx.create_osdeploy_namespace()

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
    if "hostname" in tmp:
        external_os_ip = tmp["hostname"]
    else:
        external_os_ip = tmp["ip"]
    internal_os_ip = os_service.obj["spec"]["clusterIP"]
    ctx.os_internal_ip = internal_os_ip
    ctx.os_external_ip = external_os_ip

    print("External OpenShift IP: " + external_os_ip)
    print("Internal OpenShift IP: " + internal_os_ip)

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
    conf_pod = ctx.create_config_pod(ctx.os_version)
    #conf = ctx.observe_config_pod()
    #conf_pod.delete()

    # Allow the user to edit the openshift config last second

    # Create the configs and replication controllers
    # Wait for everything to go to the ready state
    # If any crash loops happen point them out. Offer commands to debug. Link to troubleshooting.
    # Once everything is running link the public IP to access.
    # Ask if they want to setup the initial admin. If so, keep checking the user list until the first user signs in.
    # Give that user admin
    pass

@cli.command()
def undeploy():
    """Removes OpenShift from the cluster."""

@cli.command()
def config():
    """Edits the OpenShift configs interactively."""

def main():
    cli()
