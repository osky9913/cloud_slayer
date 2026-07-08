from .aws.functions import AWSLambdaProvider
from .azure.functions import AzureFunctionsProvider
from .gcp.functions import GCPFunctionsProvider

ALL_SERVERLESS_PROVIDERS = [AWSLambdaProvider(), GCPFunctionsProvider(), AzureFunctionsProvider()]
