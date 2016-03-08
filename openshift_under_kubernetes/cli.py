import os
import click
import tempfile

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

@cli.command()
@click.pass_obj
def info(ctx):
    """Show cluster information"""
    ctx.init_with_checks()

@cli.command()
@click.option("--persistent-volume", default=None, help="Path to persistent volume yaml")
@click.option("--openshift-config", default=None, help="Path to OpenShift config yaml")
@click.pass_obj
def deploy(ctx, persistent_volume, openshift_config):
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

    print("Preparing to execute deploy...")
    print("Deploy temp dir: " + ctx.temp_dir)

    # Locally pull the openshift images and verify docker works

    # Setup the deploy state namespace
    ctx.cleanup_osdeploy_namespace()
    ctx.create_osdeploy_namespace()

    # Grab the service account key
    ctx.create_servicekey_pod()

    # Get the key
    ctx.service_cert = ctx.observe_servicekey_pod()

    # Kill the pod
    ctx.delete_servicekey_pod()

    # Save the key temporarily
    with open(ctx.temp_dir + "/serviceaccounts.public.key", 'w') as f:
        f.write(ctx.service_cert)

    # Create the namespaces
    ctx.create_namespace("openshift-origin")

    # Create the templates and save them
    # Allow the user to edit the openshit config last second
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
