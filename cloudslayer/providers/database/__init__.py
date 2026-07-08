from .aws.rds import AWSRDSProvider
from .azure.postgres import AzurePostgresProvider
from .gcp.cloudsql import GCPCloudSQLProvider

ALL_DATABASE_PROVIDERS = [
    AWSRDSProvider(),
    GCPCloudSQLProvider(),
    AzurePostgresProvider(),
]
