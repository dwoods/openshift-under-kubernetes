import os
import click

from os_kube import OpenshiftKubeDeployer

kube_deployer = None
auto_confirm = False

@click.group()
@click.option("--config", default="~/.kube/config", help="kube config path", envvar="KUBE_CONFIG", type=click.Path())
@click.option("--context", default=None, help="kube context", envvar="KUBE_CONTEXT")
@click.option("--openshift-version", default="1.1.3", help="force openshift version (default 1.1.3)")
@click.option("--secure/--no-secure", default=False, help="enables https checking", envvar="KUBE_SESSION_SECURE")
@click.option("-y", help="auto-confirm yes for all questions", is_flag=True)
def cli(config, context, secure, openshift_version, y):
    auto_confirm = y
    if auto_confirm:
        print("[warn] -y is set, will automatically confirm and proceed with actions.")

    kube_deployer = OpenshiftKubeDeployer(os.path.expanduser(config), context, secure)
    if not kube_deployer.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)

@cli.command()
def info():
    """Show cluster information"""
    # We already have shown enough in the init process.

@cli.command()
def deploy():
    """Deploy OpenShift to the cluster."""
    if kube_deployer.consider_openshift_deployed:
        print("I think OpenShift is already deployed. Use undeploy first to remove it before installing it again.")
        print("Consider if you really need a full redeploy. You can update without re-deploying!")
        exit(1)
    # Figure out how to grab the service account key
    # Create the namespaces
    # Create the templates and save them, not really because we need to, but for users to view them
    # Allow the user to edit the openshit config last second
    # Create the configs and replication controllers
    # Wait for everything to go to the ready state
    # If any crash loops happen point them out. Offer commands to debug. Link to troubleshooting.
    # Once everything is running link the public IP to access.
    pass

@cli.command()
def undeploy():
    """Removes OpenShift from the cluster."""

@cli.command()
def config():
    """Edits the OpenShift configs interactively."""

if __name__ == '__main__':
    cli()
