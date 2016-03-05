import os
import click

from os_kube import OpenshiftKubeDeployer

kube_deployer = None

@click.group()
@click.option("--config", default="~/.kube/config", help="kube config path", envvar="KUBE_CONFIG", type=click.Path())
@click.option("--context", default=None, help="kube context", envvar="KUBE_CONTEXT")
@click.option("--openshift-version", default="1.1.3", help="force openshift version (default 1.1.3)")
@click.option("--secure/--no-secure", default=False, help="enables https checking", envvar="KUBE_SESSION_SECURE")
def cli(config, context, secure, openshift_version):
    kube_deployer = OpenshiftKubeDeployer(os.path.expanduser(config), context, secure)
    print("Loading kube config...")
    if not kube_deployer.load_and_check_config():
        print("Unable to load/validate config.")
        exit(1)

    print("Checking connectivity...")
    if not kube_deployer.fetch_namespaces():
        print("Connectivity looks bad, fix your connection!")
        exit(1)
    print("Everything looks good, proceeding.")
    print()
    print("Collecting some initial cluster info...")
    kube_deployer.fetch_info(skip_namespaces=True)
    kube_deployer.print_openshift_basic_status()

@cli.command()
def info():
    """Show cluster information"""
    # We already have shown enough in the init process.

if __name__ == '__main__':
    cli()
