from tools.contracts.validate import load_and_validate

def load_rules(path):
    doc, errs = load_and_validate(path, "prereq_rules")
    if errs:
        raise ValueError(f"prereq_rules schema errors: {errs}")
    ids = [r["id"] for r in doc["rules"]]
    dups = {i for i in ids if ids.count(i) > 1}
    if dups:
        raise ValueError(f"duplicate rule ids: {sorted(dups)}")
    return doc

def index_rules(doc):
    return {r["id"]: r for r in doc["rules"]}

def rules_for(doc, app_slug, market):
    out = []
    for r in doc["rules"]:
        a = r["applies_to"]
        if a["app"] in ("*", app_slug) and a["market"] == market:
            out.append(r)
    return out
