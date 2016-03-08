from pykube.objects import NamespacedAPIObject

class PersistentVolume(NamespacedAPIObject):

    version = "v1"
    endpoint = "persistentvolumes"

class PersistentVolumeClaim(NamespacedAPIObject):

    version = "v1"
    endpoint = "persistentvolumeclaims"
