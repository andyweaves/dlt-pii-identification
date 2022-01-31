# Databricks notebook source
input_path = spark.conf.get("input_path")
table_path = spark.conf.get("table_path")
expectations_path = spark.conf.get("expectations_path")

# COMMAND ----------

import json
from pyspark.sql.functions import col
 
def get_expectations(columns, expectations_file, key):
  
  results = {}
  
  with open(expectations_file, 'r') as f:
    raw_rules = json.load(f)["expectations"]
  for col in columns:
    for rule in raw_rules:
      results[rule["name"].replace("{}", f"`{col}`")] = rule[key].replace("{}", f"`{col}`")
  return results

# COMMAND ----------

columns = spark.read.parquet(input_path).columns
schema = spark.read.parquet(input_path).schema
actions = get_expectations(columns, expectations_path, 'action')
#f"file:{os.path.dirname(os.getcwd())}/expectations/pii_identification.csv"

# COMMAND ----------

from pyspark.sql.functions import explode, regexp_extract

def get_dlt_sql(actions, columns):

  # Drop duplicates because otherwise we'll need to handle duplicate columns in the downstream tables, which will get messy
  pdf = spark.read.format("delta").load(f"{table_path}/quarantine/").select(explode("failed_expectations").alias("expectation")).distinct().withColumn("failed_column", regexp_extract(col("expectation"), "\`(.*?)\`", 1)).toPandas().drop_duplicates(subset = ["failed_column"])
  
  failed_columns = pdf["failed_column"].tolist()
  failed_expectations = pdf["expectation"].tolist()
  
  print(f"Failed Columns: {failed_columns}")
  print(f"Failed Expectations: {failed_expectations}")
  
  return [x for x in columns if x not in failed_columns] + list({k: actions[k] for k in failed_expectations}.values()) 

# COMMAND ----------

import dlt

@dlt.table(
  comment="Data in which PII has been redacted based on a set of predefined rules",
  path=f"{table_path}/redacted/",
  table_properties={"may_contain_pii" : "False"}
)
def redacted():
  
  sql = get_dlt_sql(actions, columns)
  
  print(f"Dynamic SQL: {sql}")
  
  return spark.readStream.format("delta").load(f"{table_path}/quarantine/").selectExpr(sql)#.union(spark.read.format("delta").load(f"{table_path}/clean/"))

# COMMAND ----------

dbutils.fs.ls("dbfs:/dlt_pii/customer_delta")

# COMMAND ----------

@dlt.table(
  comment="Data which has either been scanned and determined not to contain PII or where PII has been identified and redacted based on a set of predefined rules",
  path=f"{table_path}/clean_processed_2/",
  table_properties={"may_contain_pii" : "False"}
)
def clean_processed():
  
  return dlt.read_stream("redacted").unionByName(spark.readStream.format("delta").load(f"{table_path}/clean/"))
