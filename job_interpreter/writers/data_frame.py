from awsglue.context import GlueContext
from awsglue.dynamicframe import DynamicFrame
from pyspark.sql import DataFrame


class DataFrameWriter:
    context: GlueContext

    def __init__(self, context: GlueContext):
        self.context = context

    def write(self, data_frame: DataFrame, options: dict):
        dynamic_df = DynamicFrame.fromDF(data_frame, self.context, "dynamic_df")
        self.context.write_dynamic_frame.from_options(
            frame=dynamic_df,
            connection_type="s3",
            connection_options={'path': options['location']},
            format=options['stored_as'],
            transformation_ctx="datasink2"
        )
