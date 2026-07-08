from .aws.s3 import AWSS3Provider
from .azure.blob import AzureBlobProvider
from .gcp.storage import GCPStorageProvider

ALL_STORAGE_PROVIDERS = [
    AWSS3Provider(),
    GCPStorageProvider(),
    AzureBlobProvider(),
]
