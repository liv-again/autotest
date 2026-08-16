import json, os
import yaml
from jsonschema import Draft202012Validator

_SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas")

def _load_schema(name):
    p = os.path.join(_SCHEMA_DIR, f"{name}.schema.json")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"no schema: {name}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def validate(doc, schema_name):
    v = Draft202012Validator(_load_schema(schema_name))
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path))]

def load_and_validate(path, schema_name):
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return doc, validate(doc, schema_name)
