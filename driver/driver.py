from typing import List

import sys, os
import traceback
from types import SimpleNamespace
import yaml
from pyspark.sql import SparkSession
from driver import task_executor
from .aws import providers
from deprecated import CatalogService

__SPARK__: SparkSession = None

from .core import compile_models, compile_product


def get_spark():
    if __SPARK__:
        return __SPARK__
    else:
        raise RuntimeError('Spark Session is not created yet. Call init() first.')


def get_or_create_session(config=None) -> SparkSession:  # pragma: no cover
    """Build spark session for jobs running on cluster."""
    spark = SparkSession.builder.appName(__name__) \
        .config(conf=config) \
        .enableHiveSupport() \
        .getOrCreate()

    return spark


def init(spark_session=None, spark_config=None):
    global __SPARK__
    if not spark_session:
        __SPARK__ = get_or_create_session(spark_config)
    else:
        __SPARK__ = spark_session
    # sc  = __SPARK__.sparkContext
    # sc.setSystemProperty("com.amazonaws.services.s3.enableV4", "true")



def load_yaml_into_object(file_type, cfg_file_prefix: str = None) -> SimpleNamespace:
    def parse(d: dict):
        x = SimpleNamespace()
        for k, v in d.items():
            if isinstance(v, dict):
                setattr(x, k, parse(v))
            elif isinstance(v, list):
                object_list = list()
                for e in v:
                    object_list.append(parse(e) if isinstance(e, dict) else e)
                setattr(x, k, object_list)
            else:
                setattr(x, str(k), v)
        return x

    path = f'{cfg_file_prefix}{file_type}' if cfg_file_prefix else file_type
    print(f'loading file {path}')
    with open(fr'{path}') as file:
        dict_val = yaml.load(file, Loader=yaml.FullLoader)
        return parse(dict_val)


def execute_tasks(product_id: str, tasks: list, models: List[SimpleNamespace], product_path: str):
    session = providers.get_session()
    if session:
        CatalogService(session).drain_database(product_id) #todo: check this implementation here if needed

    for task in tasks:
        task_executor.execute(product_id, task, models, product_path)


def process_product(args):
    try:
        # script_folder = os.path.dirname(os.path.abspath(__file__))
        rel_product_path = os.path.join(args.product_path, '') if hasattr(args, 'product_path') else os.path.join('./',
                                                                                                                  '')
        abs_product_path = os.path.join(os.path.abspath(rel_product_path), '')
        product = compile_product(load_yaml_into_object('product.yml', abs_product_path).product, args)
        models = compile_models(product, load_yaml_into_object('model.yml', abs_product_path).models)
        execute_tasks(product.id, product.pipeline.tasks, models, abs_product_path)
    except Exception as e:
        traceback.print_exc()
        print(f"Couldn't execute job due to >> {type(e).__name__}: {str(e)}")
        sys.exit(-1)
        # raise e
