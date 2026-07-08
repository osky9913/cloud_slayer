from .aws.ec2 import AWSEC2Provider
from .azure.vm import AzureComputeProvider
from .gcp.gce import GCPComputeProvider

ALL_COMPUTE_PROVIDERS = [
    AWSEC2Provider(),
    GCPComputeProvider(),
    AzureComputeProvider(),
]
