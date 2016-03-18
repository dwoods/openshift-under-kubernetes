import os
import pykube
import requests
import time
import json
import base64
import copy
import yaml
import random
import string

from pykube.config import KubeConfig
from pykube.http import HTTPClient
from pykube.objects import Pod, Namespace, Service, ReplicationController, Secret
from .more_objects import PersistentVolume, PersistentVolumeClaim

def random_string(size=6, chars=string.ascii_uppercase + string.digits):
    return ''.join(random.choice(chars) for x in range(size))

'''
Deploys OpenShift to Kubernetes
'''
class OpenshiftKubeDeployer:
    def __init__(self, config_path, context_override, enable_secure):
        self.config_path = config_path
        self.context_override = context_override
        self.enable_secure = enable_secure
        pass

    def init_with_checks(kube_deployer):
        print("Loading kube config...")
        if not kube_deployer.load_and_check_config():
            print("Unable to load/validate config.")
            return False

        print("Checking connectivity...")
        if not kube_deployer.fetch_namespaces():
            print("Connectivity looks bad, fix your connection!")
            return False

        print("Everything looks good, proceeding.")
        print()
        print("Collecting some initial cluster info...")
        kube_deployer.fetch_info(skip_namespaces=True)
        kube_deployer.print_openshift_basic_status()
        return True

    def load_and_check_config(self):
        if not os.path.exists(self.config_path):
            print("Config does not exist at path " + self.config_path + "!")
            return False
        try:
            self.config = KubeConfig.from_file(self.config_path)
        except:
            print("Config at path " + self.config_path + " failed to validate!")
            return False
        # Check current context
        if self.context_override != None:
            if self.context_override not in self.config.contexts:
                print("Context override " + self.context_override + " not in list of contexts.")
                return False
            self.config.set_current_context(self.context_override)
        elif self.config.current_context == None:
            print("Context not set, not sure which to use.")
            return False

        curr_ctx = self.config.contexts[self.config.current_context]
        self.api = HTTPClient(self.config)
        if not self.enable_secure:
            print('[note] we are in insecure mode, disabling warnings')
            requests.packages.urllib3.disable_warnings()
            self.api.session.verify = False
        return True

    def fetch_namespaces(self):
        self.namespace_list = Namespace.objects(self.api).all().response["items"]
        print("Currently there are " + str(len(self.namespace_list)) + " namespaces in the cluster.")
        if len(self.namespace_list) <= 0:
            return False
        self.namespace_names = []
        for ns in self.namespace_list:
            self.namespace_names.append(ns["metadata"]["name"])
        return True

    def fetch_openshift_setup(self):
        # Check if we have the openshift namespaces
        self.has_openshift_ns = "openshift-origin" in self.namespace_names
        # Check the replication controller
        try:
            self.openshift_rc = ReplicationController.objects(self.api).filter(namespace="openshift-origin", selector={"app": "openshift"}).response["items"][0]
        except Exception as ex:
            print(ex)
            self.openshift_rc = None
        self.consider_openshift_deployed = self.has_openshift_ns and self.openshift_rc != None

    def print_openshift_basic_status(kube_deployer):
        print("It looks like we " + ("do" if kube_deployer.has_openshift_ns else "do not") + " have the openshift-origin namespace, and " + ("do" if kube_deployer.openshift_rc != None else "do not") + " have a working ReplicationController.")
        print("Currently I " + ("do" if kube_deployer.consider_openshift_deployed else "do not") + " consider this a complete OpenShift deployment.")

    def fetch_info(self, skip_namespaces=False):
        if not skip_namespaces:
            self.fetch_namespaces()
        self.fetch_openshift_setup()

    def cleanup_osdeploy_namespace(self):
        for ns in self.namespace_list:
            if ns["metadata"]["name"] == "openshift-deploy":
                print("Cleaning up old openshift-deploy namespace.")
                self.delete_namespace_byname("openshift-deploy", True)
                return

    def build_namespace(self, name):
        return Namespace(self.api,
        {
            "metadata":
            {
                "name": name
            },
            "spec": {}
        })

    def create_namespace(self, name):
        print("Creating " + name + " namespace...")
        self.build_namespace(name).create()

    def delete_namespace_byname(self, name, wait_for_delete=False):
        print("Deleting " + name + " namespace...")
        try:
            self.build_namespace(name).delete()
        except Exception as ex:
            print("Ignoring potential error with delete: " + ex)
        if wait_for_delete:
            print("Waiting for " + name + " to terminate...")
        while wait_for_delete:
            wait_for_delete = len(Namespace.objects(self.api).filter(selector={"name": "openshift-deploy"}).response["items"]) != 0

    def create_osdeploy_namespace(self):
        self.create_namespace("openshift-deploy")


    def build_secret(self, name, namespace, kv):
        print("Creating " + name + " secret...")
        kv = copy.copy(kv)
        for k in kv:
            kv[k] = base64.b64encode(kv[k]).decode('ascii')
        return Secret(self.api,
        {
            "metadata":
            {
                "name": name,
                "namespace": namespace
            },
            "data": kv
        })

    def build_config_pod(self, os_version=""):
        if len(os_version) == 0:
            os_version = self.os_version

        print("Creating config generation pod...")
        return Pod(self.api,
        {
            "metadata":
            {
                "name": "generate-config",
                "labels":
                {
                    "purpose": "generate-config"
                },
                "namespace": "openshift-deploy"
            },
            "spec":
            {
                "containers":
                [{
                    "name": "generate-config",
                    "image": "openshift/origin:" + os_version,
                    "imagePullPolicy": "Always",
                    "command": ["/bin/bash"],
                    "args": ["/etc/config_secret_script/create-config.sh"],
                    "ports": [],
                    "env":
                    [{
                        "name": "OPENSHIFT_INTERNAL_ADDRESS",
                        "value": "https://" + self.os_internal_ip
                    }, {
                        "name": "OPENSHIFT_EXTERNAL_ADDRESS",
                        "value": "https://" + self.os_external_ip
                    }, {
                        "name": "ETCD_ADDRESS",
                        "value": "http://etcd:4001"
                    }],
                    "volumeMounts":
                    [{
                        "mountPath": "/etc/config_secret_script",
                        "name": "config-secret-script",
                        "readOnly": True
                    }, {
                        "mountPath": "/etc/kube_config",
                        "name": "kube-config",
                        "readOnly": True
                    }]
                }],
                "volumes":
                [{
                    "name": "config-secret-script",
                    "secret": {"secretName": "create-config-script"}
                }, {
                    "name": "kube-config",
                    "secret": {"secretName": "kubeconfig"}
                }],
                "restartPolicy": "Never"
            }
        })

    def build_execute_pod(self, command, admin_conf, os_version=""):
        if len(os_version) == 0:
            os_version = self.os_version

        print("Creating command execution pod...")
        name = "oskube-execute-" + random_string(4).lower()
        command = "mkdir -p ~/.kube/ && echo \"$ADMIN_KUBECONFIG\" > ~/.kube/config && " + command
        return Pod(self.api,
        {
            "metadata":
            {
                "name": name,
                "labels":
                {
                    "purpose": "exec-command"
                },
                "namespace": "openshift-origin"
            },
            "spec":
            {
                "containers":
                [{
                    "name": "exec-command",
                    "image": "openshift/origin:" + os_version,
                    "imagePullPolicy": "Always",
                    "command": ["/bin/bash"],
                    "args": ["-c", command],
                    "ports": [],
                    "env":
                    [{
                        "name": "ADMIN_KUBECONFIG",
                        "value": admin_conf
                    }]
                }],
                "restartPolicy": "Never"
            }
        })

    def wait_for_pod_succeed(self, pod):
        print("Waiting for " + pod.obj["metadata"]["name"] + " pod to finish...")
        while True:
            pod.reload()
            obj = pod.obj
            stat = obj["status"]["phase"]
            if stat in ["Running", "Pending"]:
                time.sleep(0.5)
            elif stat == "Succeeded":
                break
            else:
                raise Exception("Unexpected pod phase " + stat)

    def wait_for_pod_running(self, pod):
        print("Waiting for " + pod.obj["metadata"]["name"] + " pod to start...")
        while True:
            pod.reload()
            obj = pod.obj
            stat = obj["status"]["phase"]
            if stat in ["Pending"]:
                time.sleep(0.5)
            elif stat == "Running":
                break
            elif stat == "Succeeded":
                raise Exception("Unknown pod phase " + stat + ", expected this pod to be long-running.")
            else:
                raise Exception("Unknown pod phase " + stat)

    def observe_config_pod(self, pod, wait_for_start=True):
        if wait_for_start:
            self.wait_for_pod_succeed(pod)

        print("Checking logs...")
        url = self.api.url + "/api/v1/namespaces/openshift-deploy/pods/" + pod.obj["metadata"]["name"] + "/log"
        txt = self.api.session.get(url).text
        print("Retreived config bundle successfully.")
        return txt

    def build_os_service(self, use_load_balancer):
        return Service(self.api,
        {
            "metadata":
            {
                "name": "openshift",
                "namespace": "openshift-origin"
            },
            "spec":
            {
                "ports":
                [{
                    "name": "web",
                    "port": 443,
                    "targetPort": "web"
                }],
                "selector":
                {
                    "app": "openshift",
                    "tier": "backend"
                },
                "type": "LoadBalancer" if use_load_balancer else "NodePort"
            }
        })

    def create_os_service(self, use_load_balancer):
        print("Creating 'openshift' service...")
        srv = self.build_os_service(use_load_balancer)
        srv.create()
        return srv

    def wait_for_loadbalancer(self, service):
        while service.obj["spec"]["type"] == "LoadBalancer" \
            and ("loadBalancer" not in service.obj["status"] \
            or "ingress" not in service.obj["status"]["loadBalancer"] \
            or len(service.obj["status"]["loadBalancer"]["ingress"]) < 1 \
            or (("ip" not in service.obj["status"]["loadBalancer"]["ingress"][0] or len(service.obj["status"]["loadBalancer"]["ingress"][0]["ip"]) < 2)) and (("hostname" not in service.obj["status"]["loadBalancer"]["ingress"][0] or len(service.obj["status"]["loadBalancer"]["ingress"][0]["hostname"]) < 2))):
            time.sleep(0.5)
            service.reload()

    def fix_master_config(self, ya):
        # Fix ca
        # ya["kubeletClientInfo"]["ca"] = "ca.crt"
        ya["kubeletClientInfo"]["ca"] = ""
        ya["kubeletClientInfo"]["certFile"] = "master.kubelet-client.crt"
        ya["kubeletClientInfo"]["keyFile"] = "master.kubelet-client.key"
        ya["kubeletClientInfo"]["port"] = 10250

        # Fix etcd client
        ya["etcdClientInfo"]["ca"] = "etcd.server.crt"
        ya["etcdClientInfo"]["certFile"] = "master.etcd-client.crt"
        ya["etcdClientInfo"]["keyFile"] = "master.etcd-client.key"

        # Fix serviceAccountConfig
        ya["serviceAccountConfig"]["publicKeyFiles"] = ["serviceaccounts.public.key"]

        # Fix path to kubeconfig
        ya["masterClients"]["externalKubernetesKubeConfig"] = "external-master.kubeconfig"

        # Fix bind address
        ya["assetConfig"]["servingInfo"]["bindAddress"] = "0.0.0.0:443"
        ya["servingInfo"]["bindAddress"] = "0.0.0.0:443"

        return ya

    def build_pvc(self, name, namespace, size, create_volume):
        if not create_volume:
            return PersistentVolumeClaim(self.api,
            {
                "metadata":
                {
                    "name": name,
                    "namespace": namespace
                },
                "spec":
                {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": size}}
                }
            })
        # See https://github.com/kubernetes/kubernetes/issues/22792#issuecomment-195563296
        else:
            return PersistentVolumeClaim(self.api,
            {
                "metadata":
                {
                    "name": name,
                    "annotations":
                    {
                        "volume.alpha.kubernetes.io/storage-class": "foo"
                    },
                    "namespace": namespace
                },
                "spec":
                {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": size}}
                }
            })

    def build_etcd_rc(self, pvcn):
        return ReplicationController(self.api,
        {
            "metadata":
            {
                "name": "etcd",
                "namespace": "openshift-origin"
            },
            "spec":
            {
                "replicas": 1,
                "selector": {"app": "etcd", "purpose": "etcd"},
                "template":
                {
                    "metadata":
                    {
                        "labels": {"app": "etcd", "purpose": "etcd"}
                    },
                    "spec":
                    {
                        "containers":
                        [{
                            "name": "etcd",
                            "image": "openshift/etcd-20-centos7",
                            "ports": [{"containerPort": 4001, "name": "client"}, {"containerPort": 7001, "name": "server"}],
                            "command": ["/usr/local/bin/etcd"],
                            "args": ["--data-dir", "/var/lib/etcd", "--name", "openshift-etcd1"],
                            "env":
                            [{
                                "name": "ETCD_NUM_MEMBERS",
                                "value": "1"
                            }, {
                                "name": "ETCD_LISTEN_CLIENT_URLS",
                                "value": "http://0.0.0.0:2379,http://0.0.0.0:4001"
                            }],
                            "volumeMounts":
                            [{
                                "mountPath": "/var/lib/etcd",
                                "name": "etcd-storage"
                            }]
                        }],
                        "volumes":
                        [{
                            "name": "etcd-storage",
                            "persistentVolumeClaim": {"claimName": pvcn}
                        }],
                        "restartPolicy": "Always",
                        "dnsPolicy": "ClusterFirst"
                    }
                }
            }
        })

    def build_etcd_service(self):
        return Service(self.api,
        {
            "metadata":
            {
                "name": "etcd",
                "namespace": "openshift-origin"
            },
            "spec":
            {
                "ports":
                [{
                    "name": "client",
                    "port": 4001,
                    "targetPort": "client"
                }, {
                    "name": "server",
                    "port": 7001,
                    "targetPort": "server"
                }],
                "selector":
                {
                    "app": "etcd",
                    "purpose": "etcd"
                },
                "type": "ClusterIP"
            }
        })

    def build_registry_svc(self, namespace):
        return Service(self.api,
        {
            "metadata":
            {
                "labels":
                {
                    "app": "registry",
                    "role": "registry",
                    "tier": "backend",
                    # Not sure if this label is important
                    "docker-registry": "default"
                },
                "name": "docker-registry",
                "namespace": namespace
            },
            "spec":
            {
                "ports":
                [{
                    "name": "registry",
                    "targetPort": "registry",
                    "port": 5000,
                    "protocol": "TCP"
                }],
                "selector":
                {
                    "docker-registry": "default"
                }
            }
        })

    def build_registry_rc(self, ca_data, client_cert_data, client_key_data, server, namespace, pvcn, registry_image):
        return ReplicationController(self.api,
        {
            "metadata":
            {
                "labels":
                {
                    "app": "registry",
                    "role": "registry",
                    "tier": "backend",
                    # Not sure if this label is important
                    "docker-registry": "default"
                },
                "name": "docker-registry",
                "namespace": namespace
            },
            "spec":
            {
                "replicas": 1,
                "selector":
                {
                    "app": "registry",
                    "role": "registry",
                    "tier": "backend",
                    "docker-registry": "default"
                },
                "template":
                {
                    "metadata":
                    {
                        # I believe selector is filled with this on default
                        # Consider removing the extra info
                        "labels":
                        {
                            "app": "registry",
                            "role": "registry",
                            "tier": "backend",
                            "docker-registry": "default"
                        }
                    },
                    "spec":
                    {
                        "containers":
                        [{
                            "name": "registry",
                            "image": registry_image + ":" + self.os_version,
                            "imagePullPolicy": "IfNotPresent",
                            "livenessProbe":
                            {
                                "failureThreshold": 3,
                                "httpGet":
                                {
                                    "path": "/healthz",
                                    "port": 5000,
                                    "scheme": "HTTP"
                                },
                                "initialDelaySeconds": 10,
                                "periodSeconds": 10,
                                "successThreshold": 1,
                                "timeoutSeconds": 5
                            },
                            "ports": [{"containerPort": 5000, "protocol": "TCP", "name": "registry"}],
                            "volumeMounts": [{"mountPath": "/registry", "name": "registry-storage"}],
                            "env":
                            [{
                                "name": "OPENSHIFT_CA_DATA",
                                "value": ca_data
                            }, {
                                "name": "OPENSHIFT_CERT_DATA",
                                "value": client_cert_data
                            }, {
                                "name": "OPENSHIFT_INSECURE",
                                "value": "true" if server.startswith("http://") else "false"
                            }, {
                                "name": "OPENSHIFT_KEY_DATA",
                                "value": client_key_data
                            }, {
                                "name": "OPENSHIFT_MASTER",
                                "value": server
                            }, {
                                "name": "KUBERNETES_MASTER",
                                "value": server
                            }, {
                                # I'm guessing this tricks the registry into binding to port 5000
                                # Without actually setting an address
                                "name": "REGISTRY_HTTP_ADDR",
                                "value": ":5000"
                            }, {
                                "name": "REGISTRY_HTTP_NET",
                                "value": "tcp"
                            }, {
                                "name": "REGISTRY_HTTP_SECRET",
                                "value": random_string(10)
                            }]
                        }],
                        "volumes":
                        [{
                            "name": "registry-storage",
                            "persistentVolumeClaim": {"claimName": pvcn}
                        }],
                        "restartPolicy": "Always",
                        "dnsPolicy": "ClusterFirst"
                    }
                }
            }
        })

    def build_openshift_rc(self, os_version):
        return ReplicationController(self.api,
        {
            "metadata":
            {
                "name": "openshift",
                "namespace": "openshift-origin",
                "labels": {"app": "openshift", "tier": "backend"}
            },
            "spec":
            {
                "replicas": 1,
                "selector": {"app": "openshift", "tier": "backend"},
                "template":
                {
                    "metadata":
                    {
                        "labels": {"app": "openshift", "tier": "backend"}
                    },
                    "spec":
                    {
                        "containers":
                        [{
                            "image": "openshift/origin:" + os_version,
                            "name": "origin",
                            "args": ["start", "master", "--config=/config/master-config.yaml"],
                            "ports": [{"containerPort": 443, "name": "web"}],
                            "volumeMounts": [{"mountPath": "/config", "name": "config", "readOnly": True}]
                        }],
                        "volumes":
                        [{
                            "name": "config",
                            "secret": {"secretName": "openshift-config"}
                        }]
                    }
                }
            }
        })

    def find_persistentvolume(self, pvname):
        print("Checking for persistentvolume " + pvname + "...")
        try:
            url = self.api.url + "/api/v1/persistentvolumes/" + pvname
            res = self.api.session.get(url)
            if res.status_code != 200:
                return None
            return PersistentVolume(self.api, json.loads(res.text))
        except:
            return None

    def fetch_config_to_dir(self, config_dir):
        print("Fetching config files to dir " + config_dir)
        config_secret = self.build_secret("openshift-config", "openshift-origin", {})
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
        return config_secret

    def escalate_admin_kubeconfig(self, temp_dir):
        # Fist we need to fetch the config
        ctx = self
        if not self.init_with_checks():
            print("Failed cursory checks.")
            return False

        if not ctx.consider_openshift_deployed:
            print("I think OpenShift is not yet deployed. Use deploy first to create it.")
            return False

        secret = ctx.fetch_config_to_dir(temp_dir)
        conf_path = temp_dir + "/admin.kubeconfig"
        mconf_path = temp_dir + "/master-config.yaml"
        if not os.path.exists(conf_path) or not os.path.exists(mconf_path):
            print("Fetched config files but they don't contain admin.kubeconfig, something's wrong. Try getconfig.")
            exit(1)

        print("Escalating to system:admin via admin.kubeconfig...")
        ctx.config_path = conf_path

        # Do an initial load to verify that it is valid
        self.context_override = None
        if not self.load_and_check_config():
            print("Unable to load/validate config.")
            return False

        # Pick the context for the public facing endpoint
        master_conf = None
        with open(mconf_path, 'r') as f:
            master_conf = yaml.load(f)

        pubUrl = master_conf["assetConfig"]["masterPublicURL"]
        cctx = self.config.current_context
        found_ctx = None
        for ctx in self.config.contexts:
            clun = self.config.contexts[ctx]["cluster"]
            if not clun in self.config.clusters:
                print("[warn] Cluster " + clun + " not valid, but in context " + ctx)
                continue

            # Grab the cluster
            clu = self.config.clusters[clun]
            if clu == None:
                continue
            if clu["server"] == pubUrl:
                print("Found context " + ctx + " for public-facing endpoint " + pubUrl)
                found_ctx = ctx
                break

        if found_ctx == None:
            print("[warn] Unable to find a context matching public url " + pubUrl + ", continuing with " + cctx)
        else:
            cctx = found_ctx
        self.context_override = cctx

        print("Re-checking connection with escalated permissions...")
        # This time we will override the context
        if not self.init_with_checks():
            print("Failed checks, make sure admin.kubeconfig is valid.")
            return False

        # We are now using the OpenShift admin connection.
        return True

    # Checks the OpenShift users list
    def get_openshift_users(self):
        url = self.api.url + "/oapi/v1/users"
        res = self.api.session.get(url).text
        return json.loads(res)["items"]

    # Checks the openshift cluster role binding list
    def get_openshift_cluster_rolebindings(self):
        url = self.api.url + "/oapi/v1/clusterrolebindings"
        res = self.api.session.get(url).text
        return json.loads(res)["items"]

    def put_openshift_cluster_rolebinding(self, rb):
        url = self.api.url + "/oapi/v1/clusterrolebindings/" + rb["metadata"]["name"]
        self.api.session.put(url, json=rb)
