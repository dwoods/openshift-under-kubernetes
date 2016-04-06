import os
import click
import tempfile
import yaml
import base64
import tarfile
import time
import shutil
import random

from pkg_resources import resource_string, resource_listdir
from subprocess import call
from .os_kube import OpenshiftKubeDeployer
from .more_objects import PersistentVolume
from .util import deepupdate
from pykube.objects import Pod
from pykube.config import KubeConfig

EDITOR = os.environ.get('EDITOR','vim')

@click.group()
@click.option("--config", default="~/.kube/config", help="kube config path", envvar="KUBE_CONFIG", type=click.Path())
@click.option("--context", default=None, help="kube context", envvar="KUBE_CONTEXT")
@click.option("--openshift-version", default="1.1.6", help="force openshift version (default 1.1.6)")
@click.option("--secure/--no-secure", default=False, help="enables https checking", envvar="KUBE_SESSION_SECURE")
@click.option("-y", help="auto-answer for all confirmations", is_flag=True)
@click.pass_context
def cli(ctx, config, context, secure, openshift_version, y):
    ctx.auto_confirm = y
    if ctx.auto_confirm:
        print("[warn] -y is set, will automatically confirm and proceed with actions.")

    ctx.kube_deployer = OpenshiftKubeDeployer(os.path.expanduser(config), context, secure)
    ctx.obj = ctx.kube_deployer
    ctx.obj.auto_confirm = y
    ctx.obj.os_version = openshift_version
    if ctx.obj.os_version.find("v") != 0:
        ctx.obj.os_version = "v" + ctx.obj.os_version
    ctx.obj.scripts_resource = __name__.split('.')[0] + ".resources.scripts"

@cli.command()
@click.pass_obj
def info(ctx):
    """Show cluster information"""
    exit(0 if ctx.init_with_checks() and ctx.consider_openshift_deployed else 1)

@cli.command()
@click.option("--persistent-volume", default="openshift-etcd1", help="Name of existing PersistentVolume of at least 2Gi size for storage")
@click.option("--create-volume/--no-create-volume", default=False, help="tell Kubernetes to create the volume (alpha feature)", envvar="OPENSHIFT_AUTOCREATE_VOLUME")
@click.option("--public-hostname", default=None, help="public hostname that will be DNSd to the public IP", envvar="OPENSHIFT_PUBLIC_DNS")
@click.option("--load-balancer/--no-load-balancer", default=True, help="use load balancer, otherwise node port", envvar="OPENSHIFT_CREATE_LOADBALANCER")
@click.option("--master-config-override", default=None, help="file with master-config.yaml overrides", envvar="OPENSHIFT_MASTER_CONFIG", type=click.Path(exists=True))
@click.option("--server-key", help="server.key from /srv/kubernetes/server.key", envvar="OPENSHIFT_SERVER_KEY_PATH", type=click.Path(exists=True))
@click.pass_obj
def deploy(ctx, persistent_volume, load_balancer, public_hostname, create_volume, master_config_override, server_key):
    """Deploy OpenShift to the cluster."""
    if not load_balancer and public_hostname != None:
        print("You must specify --load-balancer with --public-hostname, I can't map a public hostname without a load balancer.")
        exit(1)

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

    ctx.temp_dir = tempfile.mkdtemp()
    print("Preparing to execute deploy...")
    print("Deploy temp dir: " + ctx.temp_dir)

    # Setup the deploy state namespace
    ctx.cleanup_osdeploy_namespace()
    ctx.create_osdeploy_namespace()

    # Check the persistentvolume exists
    if not create_volume and ctx.find_persistentvolume(persistent_volume) == None:
        print(" [!] persistentvolume with name " + persistent_volume + " does not exist. Did you create it?")
        exit(1)

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

    internal_os_ip = os_service.obj["spec"]["clusterIP"]
    ctx.os_internal_ip = internal_os_ip

    if load_balancer:
        tmp = os_service.obj["status"]["loadBalancer"]["ingress"][0]
        external_os_ip = None
        external_is_hostname = False
        if "hostname" in tmp:
            external_os_ip = tmp["hostname"]
            external_is_hostname = True
        else:
            external_os_ip = tmp["ip"]
        ctx.os_external_ip = external_os_ip
        print("External OpenShift IP: " + external_os_ip)
    else:
        external_os_ip = internal_os_ip
        print("External OpenShift IP: nodes (node port)")

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
        conf = yaml.load(f)
    conf = ctx.fix_master_config(conf)

    # Write the serviceaccounts file again
    with open(server_key, 'r') as fs:
        with open(ctx.temp_dir + "/config/serviceaccounts.public.key", 'w') as fd:
            fd.write(fs.read())

    # Load patches if needed
    master_config_override_kv = None
    if master_config_override != None:
        print("Loading " + master_config_override + "...")
        with open(master_config_override, 'r') as f:
            master_config_override_kv = yaml.load(f)
        conf = deepupdate(conf, master_config_override_kv)

    # Write the fixed master config
    with open(ctx.temp_dir + "/config/master-config.yaml", 'w') as f:
        f.write(yaml.dump(conf, default_flow_style=False))

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
    etcd_pvc = ctx.build_pvc("openshift-etcd1", "openshift-origin", "2Gi", create_volume)
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

    ctx.fetch_namespaces()
    ctx.cleanup_osdeploy_namespace()
    shutil.rmtree(ctx.temp_dir)

@cli.command()
@click.pass_obj
def undeploy(ctx):
    """Removes OpenShift from the cluster."""
    print("Preparing to remove OpenShift...")
    if not ctx.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)

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
@click.option("--config-output-dir", default=".", help="directory to write the openshift config", envvar="KUBE_CONFIG_OUTPUT_DIR", type=click.Path(exists=True), prompt=True)
@click.pass_obj
def getconfig(ctx, config_output_dir):
    """Writes the entire openshift config to a directory for inspection."""
    config_dir = config_output_dir
    if not ctx.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)

    if not ctx.consider_openshift_deployed:
        print("I think OpenShift is not yet deployed. Use deploy first to create it.")
        exit(1)
    ctx.fetch_config_to_dir(config_dir)

@cli.command()
@click.option("--persistent-volume", default="openshift-registry", help="Name of existing PersistentVolume of at least 2Gi size for storage")
@click.option("--create-volume/--no-create-volume", default=False, help="tell Kubernetes to create the volume (alpha feature)", envvar="OPENSHIFT_AUTOCREATE_REGISTRY_VOLUME")
@click.option("--volume-size", default="10Gi", help="how big should the storage for the registry be", envvar="OPENSHIFT_REGISTRY_VOLUME_SIZE")
@click.option("--registry-image", default="openshift/origin-docker-registry", help="registry image name", envvar="OPENSHIFT_REGISTRY_IMAGE")
@click.pass_obj
def deployregistry(ctx, persistent_volume, create_volume, volume_size, registry_image):
    """Deploy an OpenShift Registry to the cluster."""
    if not ctx.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)

    if not ctx.consider_openshift_deployed:
        print("OpenShift doesn't seem to be deployed.")
        print("Please deploy it first.")
        exit(1)

    # Build temporary object to check existence
    reg_rc = ctx.build_registry_rc("", "", "", "", "default", "registry-storage", "")
    if reg_rc.exists():
        print("The replication controller 'docker-registry' exists in 'default' namespace, this indicates a potentially existing/broken registry.")
        print("Refusing to continue, you should address this manually.")
        exit(1)

    print()

    ctx.temp_dir = tempfile.mkdtemp()
    print("Preparing to execute deploy...")
    print("Deploy temp dir: " + ctx.temp_dir)

    # Check the persistentvolume exists
    if not create_volume and ctx.find_persistentvolume(persistent_volume) == None:
        print(" [!] persistentvolume with name " + persistent_volume + " does not exist. Did you create it?")
        exit(1)

    # Fetch the config to the local dir
    ctx.fetch_config_to_dir(ctx.temp_dir)

    master_conf = None
    with open(ctx.temp_dir + "/master-config.yaml", 'r') as f:
        master_conf = yaml.load(f)
    internal_url = master_conf["oauthConfig"]["masterURL"]

    cluster_ca = None
    with open(ctx.temp_dir + "/ca.crt", 'r') as f:
        cluster_ca = f.read()

    client_key = None
    client_cert = None
    with open(ctx.temp_dir + "/openshift-registry.kubeconfig", 'r') as f:
        reg_conf = yaml.load(f)
        client_key = base64.b64decode(reg_conf["users"][0]["user"]["client-key-data"]).decode('ascii')
        client_cert = base64.b64decode(reg_conf["users"][0]["user"]["client-certificate-data"]).decode('ascii')

    # Create the namespaces
    #ctx.create_namespace("default")

    # Create the pvc
    reg_pvc = ctx.build_pvc("registry-storage", "default", volume_size, create_volume)
    if not reg_pvc.exists():
        reg_pvc.create()

    # Build the registry replication controller
    # I do this this way because I would prefer to not use a deployment here.
    reg_rc = ctx.build_registry_rc(cluster_ca, client_cert, client_key, internal_url, "default", "registry-storage", registry_image)
    # Needs a securitycontext that allows this
    if create_volume:
        reg_rc.obj["spec"]["template"]["spec"]["securityContext"] = {"fsGroup": 1001}
    # reg_rc.obj["spec"]["template"]["spec"]["securityContext"] = {"runAsUser": 0}
    reg_rc.create()

    # Create the service
    reg_svc = ctx.build_registry_svc("default")
    if not reg_svc.exists():
        reg_svc.create()

    print("Done!")
    shutil.rmtree(ctx.temp_dir)

@cli.command()
@click.pass_obj
def editconfig(ctx):
    """Interactively edits master-config.yaml"""
    ctx.temp_dir = tempfile.mkdtemp()
    if ctx.auto_confirm:
        print("Note: -y option is not supported for purely interactive commands.")
        ctx.auto_confirm = False

    if not ctx.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)

    if not ctx.consider_openshift_deployed:
        print("I think OpenShift is not yet deployed. Use deploy first to create it.")
        exit(1)

    old_secret = ctx.fetch_config_to_dir(ctx.temp_dir)
    mc_path = ctx.temp_dir + "/master-config.yaml"
    if not os.path.exists(mc_path):
        print("Fetched config files but they don't contain master-config.yaml, something's wrong. Try getconfig.")
        shutil.rmtree(ctx.temp_dir)
        exit(1)

    last_mtime = os.path.getmtime(mc_path)
    print("Config files are at: " + ctx.temp_dir)
    print("Feel free to edit as you will...")
    print("Launching editor...")
    call([EDITOR, mc_path])
    now_mtime = os.path.getmtime(mc_path)
    if now_mtime == last_mtime:
        print("No changes made, exiting.")
        shutil.rmtree(ctx.temp_dir)
        exit(0)

    if not click.confirm("Do you want to upload the changed config files?"):
        print("Okay, cancelling.")
        shutil.rmtree(ctx.temp_dir)
        exit(0)

    print("Preparing to upload config files...")
    # Serialize the config to a secret
    openshift_config_kv = {}
    for filen in os.listdir(ctx.temp_dir):
        with open(ctx.temp_dir + "/" + filen, 'rb') as f:
            openshift_config_kv[filen] = f.read()
    openshift_config_secret = ctx.build_secret("openshift-config", "openshift-origin", openshift_config_kv)
    openshift_config_secret._original_obj = old_secret.obj

    print("Attempting to patch secret...")
    openshift_config_secret.update()
    print("Updates applied.")

    if not click.confirm("Do you want to restart openshift to apply the changes?"):
        print("Okay, I'm done. Have a nice day!")
        shutil.rmtree(ctx.temp_dir)
        exit(0)

    print("Restarting openshift pod...")
    try:
        pods = Pod.objects(ctx.api).filter(namespace="openshift-origin", selector={"app": "openshift"}).response["items"]
        if len(pods) >= 1:
            openshift_pod = Pod(ctx.api, pods[0])
            print("Deleting pod " + openshift_pod.obj["metadata"]["name"] + "...")
            openshift_pod.delete()
    except:
        print("Something went wrong restarting openshift, do it yourself please!")

    shutil.rmtree(ctx.temp_dir)

@cli.command()
@click.option("--username", default=None, prompt=True, help="username to alter", envvar="OPENSHIFT_TARGET_USERNAME")
@click.option("--role", prompt=True, default="cluster-admins", help="role to add", envvar="OPENSHIFT_TARGET_ROLE")
@click.pass_obj
def addclusterrole(ctx, username, role):
    """Adds a CLUSTER role to a user by directly querying the cluster."""
    if role != "cluster-admins":
        print("[warn] It's really not advisable to assign most cluster-wide roles to users.")

    admin_username = username
    ctx.temp_dir = tempfile.mkdtemp()
    if not ctx.escalate_admin_kubeconfig(ctx.temp_dir):
        print("Unable to escalate permissions using admin.kubeconfig, make sure it's valid.")
        exit(1)

    # We have admin permissions in openshift now.
    shutil.rmtree(ctx.temp_dir)

    # Attempt to query the users list
    # PyKube doesn't really support OpenShift so let's build urls ourself
    user_list = ctx.get_openshift_users()
    known_uid = []
    user = None
    for usr in user_list:
        name = usr["metadata"]["name"]
        known_uid.append(name)
        if name == admin_username:
            user = usr
    if user == None:
        print("Unable to find user '" + admin_username + "' in username list.")
        print("Known users: " + ", ".join(known_uid))
        exit(1)

    # User exists, check his roles
    if user["groups"] == None:
        user["groups"] = ["none"]
    print("Found user " + admin_username + ", groups: " + ", ".join(user["groups"]))

    # Add role
    cluster_role_bindings = ctx.get_openshift_cluster_rolebindings()
    mod_role = None
    for rolex in cluster_role_bindings:
        if rolex["metadata"]["name"] == role:
            mod_role = rolex
            break
    if mod_role == None:
        print("Unable to find cluster role named '" + role + "', try adding an s to the end of it.")
        exit(1)

    # Check the usernames
    if mod_role["userNames"] == None:
        mod_role["userNames"] = []
    unames = mod_role["userNames"]
    if admin_username in unames:
        print("User " + admin_username + " already in role binding " + mod_role["metadata"]["name"])
        print("Skipping update...")
    else:
        unames.append(admin_username)
        # patch the role binding
        print("Patching role binding...")
        ctx.put_openshift_cluster_rolebinding(mod_role)

    print("Done.")

@cli.command()
@click.option("--command", default="openshift ex diagnostics", help="command to run")
@click.pass_obj
def execute(ctx, command):
    """Executes any of the OpenShift commands inside a pod with the admin kubeconfig mounted."""
    if not ctx.init_with_checks():
        print("Failed cursory checks, exiting.")
        exit(1)

    if not ctx.consider_openshift_deployed:
        print("I think OpenShift is not yet deployed. Use deploy first to create it.")
        exit(1)

    # First grab the config to a folder
    ctx.temp_dir = tempfile.mkdtemp()
    ctx.fetch_config_to_dir(ctx.temp_dir)

    # Read the admin.kubeconfig
    admincfg = None
    with open(ctx.temp_dir + "/admin.kubeconfig", 'r') as f:
        admincfg = f.read()
    shutil.rmtree(ctx.temp_dir)

    # Create the pod
    exec_pod = ctx.build_execute_pod(command, admincfg)
    exec_pod.create()

    print()
    print("Remember to delete this pod after it's done executing.")
    print("You can clean up all execute pods with this command:")
    print("kubectl delete -l purpose=exec-command --namespace=openshift-origin")
    print()
    print("To view the result, use the command:")
    print("kubectl logs --namespace=openshift-origin -f " + exec_pod.obj["metadata"]["name"])

def main():
    cli()
