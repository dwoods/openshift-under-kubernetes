import os
import click

from os_kube import OpenshiftKubeDeployer

@click.group()
@click.option("--config", default="~/.kube/config", help="kube config path", envvar="KUBE_CONFIG", type=click.Path())
@click.option("--context", default=None, help="kube context", envvar="KUBE_CONTEXT")
@click.option("--openshift-version", default="1.1.3", help="force openshift version (default 1.1.3)")
@click.option("--secure/--no-secure", default=False, help="enables https checking", envvar="KUBE_SESSION_SECURE")
@click.option("--interactive", is_flag=True, help="interactively configure openshift", envvar="KUBE_OPENSHIFT_INTERACTIVE")
@click.option("-y", help="auto-answer for all confirmations", is_flag=True)
@click.pass_context
def cli(ctx, config, context, secure, openshift_version, y, interactive):
    ctx.auto_confirm = y
    ctx.is_interactive = interactive
    if ctx.auto_confirm:
        print("[warn] -y is set, will automatically confirm and proceed with actions.")

    ctx.kube_deployer = OpenshiftKubeDeployer(os.path.expanduser(config), context, secure)
    if not ctx.kube_deployer.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)
    ctx.obj = ctx.kube_deployer

@cli.command()
def info():
    """Show cluster information"""
    # We already have shown enough in the init process.

@cli.command()
@click.pass_obj
def deploy(ctx):
    """Deploy OpenShift to the cluster."""
    if ctx.consider_openshift_deployed:
        print("I think OpenShift is already deployed. Use undeploy first to remove it before installing it again.")
        print("Consider if you really need a full redeploy. You can update without re-deploying!")
        exit(1)

    print("Preparing to execute deploy...")

    # Setup the deploy state namespace
    ctx.cleanup_osdeploy_namespace()
    ctx.create_osdeploy_namespace()

    # Grab the service account key
    ctx.create_servicekey_pod()

    # Get the key
    ctx.service_cert = ctx.observe_servicekey_pod()

    # Create the namespaces
    # Create the templates and save them, not really because we need to, but for users to view them
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

if __name__ == '__main__':
    cli()
