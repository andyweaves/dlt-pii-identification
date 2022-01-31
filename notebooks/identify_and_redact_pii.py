# Databricks notebook source
input_path = spark.conf.get("input_path")
table_path = spark.conf.get("table_path")
expectations_path = spark.conf.get("expectations_path")

# COMMAND ----------

#dbutils.fs.rm("dbfs:/tmp/sql.txt")

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
constraints = get_expectations(columns, expectations_path, 'constraint')
actions = get_expectations(columns, expectations_path, 'action')

# When DLT supports repos we'll be able to use this and it'll be much easier... For now the expectations are in https://e2-demo-west.cloud.databricks.com/?o=2556758628403379#files/2035247281457633
#f"file:{os.path.dirname(os.getcwd())}/expectations/pii_detection.csv"

# COMMAND ----------

import dlt

@dlt.view(
  name="staging",
  comment="Raw data that may contain PII"
)
def staging():
  return (
    spark.read.parquet(input_path)
  )

# COMMAND ----------

from pyspark.sql.functions import udf

@udf("array<string>")
def get_failed_expectations(expectations):
  # retrieve the name of each failed expectation 
  return [name for name, success in zip(constraints, expectations) if not success]

# COMMAND ----------

@dlt.table(
  name="clean",
  comment="Clean data that has been scanned and determined not to contain PII",
  path=f"{table_path}/clean/",
  table_properties={"may_contain_pii" : "False"}
)
@dlt.expect_all_or_drop(constraints) 
def clean():
  return dlt.read("staging")

# COMMAND ----------

import pyspark.sql.functions as F
  
@dlt.view(
 name="quarantine",
 comment="Data that has been scanned and quarantined for potentially containing PII"
)
def quarantine():
  
  return (
      dlt
        .read("staging")
        .withColumn("failed_expectations", F.array([F.expr(value) for key, value in constraints.items()]))
        .withColumn("failed_expectations", get_failed_expectations("failed_expectations"))
        .filter(F.size("failed_expectations") > 0)
  )

# COMMAND ----------

from pyspark.sql.functions import explode, regexp_extract

def get_dlt_sql(actions, columns):

  # Drop duplicates because otherwise we'll need to handle duplicate columns in the downstream tables, which will get messy
  pdf = spark.sql("SELECT * FROM LIVE.staging").withColumn("failed_expectations", F.array([F.expr(value) for key, value in constraints.items()])).withColumn("failed_expectations", get_failed_expectations("failed_expectations")).filter(F.size("failed_expectations") > 0).select(explode("failed_expectations").alias("expectation")).distinct().withColumn("failed_column", regexp_extract(col("expectation"), "\`(.*?)\`", 1)).toPandas().drop_duplicates(subset = ["failed_column"])
  
  failed_columns = pdf["failed_column"].tolist()
  failed_expectations = pdf["expectation"].tolist()
  
  return [x for x in columns if x not in failed_columns] + list({k: actions[k] for k in failed_expectations}.values()) 

# COMMAND ----------

def list_to_file(file, l):

  with open(file, 'w') as f:
    for item in l:
        f.write('%s\n' % item)

# COMMAND ----------

def file_to_list(file):
  
  l = []
  with open(file, 'r') as f:
    for line in f:
      current_line = line[:-1]
      l.append(current_line)
  return l

# COMMAND ----------

sql = file_to_list('/dbfs/tmp/sql.txt')

# COMMAND ----------

from datetime import datetime

@dlt.table(
  name="redacted",
  comment="Data in which PII has been redacted based on a set of predefined rules",
  path=f"{table_path}/redacted/",
  table_properties={"may_contain_pii" : "False"}
)
def redacted():
  
  #sql = get_dlt_sql(actions, columns)
  
  #sql_bc = spark.sparkContext.broadcast(sql) 
  
  #list_to_file('/dbfs/tmp/sql.txt', get_dlt_sql(actions, columns))
  
  #list_to_file('/dbfs/tmp/sql.txt', get_dlt_sql(actions, columns))
  #sql = file_to_list('/dbfs/tmp/sql.txt')
  print(f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')} SQL: {sql}")
  
  return dlt.read("quarantine").selectExpr(sql + ["failed_expectations"])

# COMMAND ----------

@dlt.table(
  name="clean_processed",
  comment="Data which has either been scanned and determined not to contain PII or where PII has been identified and redacted based on a set of predefined rules",
  path=f"{table_path}/clean_processed/",
  table_properties={"may_contain_pii" : "False"}
)
def clean_processed():
  
  return dlt.read("redacted").drop("failed_expectations").unionByName(spark.table("LIVE.clean"))

# COMMAND ----------

# MAGIC %sql
# MAGIC --SELECT * FROM wip_pii.redacted
