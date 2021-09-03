import hashlib
import quinn
from driver import common
from pyspark.sql import DataFrame
from pyspark.sql.functions import col, count, when, lit, udf, hash
from pyspark.sql.types import StringType
from pyspark.ml.feature import Bucketizer
from driver.core import ValidationException
from driver.task_executor import DataSet
from quinn.dataframe_validator import (
    DataFrameMissingStructFieldError,
    DataFrameMissingColumnError,
    DataFrameProhibitedColumnError
)
from driver import aws_provider
from .catalog import CatalogService


def null_validator(df: DataFrame, col_name: str, cfg: any = None):
    # null_value_ratio = df.select(count(when(col(col_name).isNull(), True)) / count(lit(1)).alias('count')) \
    #     .first()[0]
    # ('not_null', self.column, null_value_ratio <= self.threshold, self.threshold, null_value_ratio

    if df.filter((df[col_name].isNull()) | (df[col_name] == "")).count() > 0:
        raise ValidationException(f'Column: {col_name} is expected to be not null.')


def regexp_validator(df: DataFrame, col_name: str, cfg: any = None):
    if df.select(col(col_name)).count() != df.select(col(col_name).rlike(cfg.value)).count():
        raise ValidationException(f"Column: {col_name} doesn't match regexp: {cfg.value}")


def unique_validator(df: DataFrame, col_name: str, cfg: any = None):
    col = df.select(col_name)
    if col.distinct().count() != col.count():
        raise ValidationException(f'Column: {col_name} is expected to be unique.')


constraint_validators = {
    "not_null": null_validator,
    "unique": unique_validator,
    "regexp": regexp_validator
}


def hasher(df: DataFrame, col_name: str, cfg: any = None) -> DataFrame:
    return df.withColumn(col_name, hash(col(col_name)))


def encrypt(df: DataFrame, col_name: str, cfg: any = None) -> DataFrame:
    def encrypt_f(value: object, key: str = None):
        if key:
            return hashlib.sha256(str(value).encode() + key.encode()).hexdigest()
        else:
            return hashlib.sha256(str(value).encode()).hexdigest()

    encrypt_udf = udf(encrypt_f, StringType())
    return df.withColumn(col_name, encrypt_udf(col_name, lit(cfg.key if hasattr(cfg, 'key') else None)))


def skip_column(df: DataFrame, col_name: str, cfg: any = None) -> DataFrame:
    return df.drop(col(col_name))


def rename_col(df: DataFrame, col_name: str, cfg: any = None) -> DataFrame:
    return df.withColumnRenamed(col_name, cfg.name)


def bucketize(df: DataFrame, col_name: str, cfg: any = None) -> DataFrame:
    buckets = cfg.buckets.__dict__
    bucket_labels = dict(zip(range(len(buckets.values())), buckets.values()))
    bucket_splits = [float(split) for split in buckets.keys()]
    bucket_splits.append(float('Inf'))

    bucketizer = Bucketizer(splits=bucket_splits, inputCol=col_name, outputCol="tmp_buckets")
    bucketed = bucketizer.setHandleInvalid("keep").transform(df)

    udf_labels = udf(lambda x: bucket_labels[x], StringType())
    bucketed = bucketed.withColumn(col_name, udf_labels("tmp_buckets"))
    bucketed = bucketed.drop(col('tmp_buckets'))

    return bucketed


built_in_transformers = {
    'anonymize': hasher,
    'encrypt': encrypt,
    'skip': skip_column,
    'bucketize': bucketize,
    'rename_column': rename_col
}


def schema_checker(ds: DataSet):
    try:
        if ds.model:
            ds_schema = common.remap_schema(ds)
            quinn.validate_schema(ds.df, ds_schema)
    except (DataFrameMissingColumnError, DataFrameMissingStructFieldError, DataFrameProhibitedColumnError) as ex:
        raise ValidationException(f'Schema Validation Error: {str(ex)} of type: {type(ex).__name__}')
    return ds


def constraint_processor(ds: DataSet):
    if not hasattr(ds, 'model'):
        return ds

    for col in ds.model.columns:
        if not hasattr(col, 'constraints'):
            continue
        constraint_types = [c.type for c in col.constraints]
        for ctype in constraint_types:
            cvalidator = constraint_validators.get(ctype)
            if cvalidator:
                constraint = next(iter([co for co in col.constraints if co.type == ctype]), None)
                constraint_opts = constraint.options if hasattr(constraint, 'options') else None
                cvalidator(ds.df, col.id, constraint_opts)
    return ds


def transformer_processor(data_set: DataSet):
    if not hasattr(data_set, 'model'):
        return data_set
    for col in data_set.model.columns:
        if not hasattr(col, 'transform'):
            continue
        transformers = [t.type for t in col.transform]
        for trsfrm_type in transformers:
            tcall = built_in_transformers.get(trsfrm_type)
            if tcall:
                trsfrm = next(iter([to for to in col.transform if to.type == trsfrm_type]), None)
                trsfm_opts = trsfrm.options if trsfrm and hasattr(trsfrm, 'options') else None
                data_set.df = tcall(data_set.df, col.id, trsfm_opts)
    return data_set


def catalog_processor(data_set: DataSet):
    catalog_service = CatalogService(aws_provider.get_session())
    catalog_service.update_database(data_set.product_id, data_set.model_id, data_set)
    return data_set
