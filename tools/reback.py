"""结构化 upsert 反哺（spec §8.6）：把一轮测试/使用中产生的新条目按唯一标识字段
合并进机器权威 yaml（apps/<app>/profile.yaml、prerequisites.yaml），不产生重复项。
写盘前用 tools/contracts/validate 做 schema 校验，任一文档校验失败则两个文件均不写。"""
import yaml

from tools.contracts.validate import validate

# 各 section 的声明标识字段（按 Task 3 数据模型）。profile.yaml 的 entries/capabilities/
# verified_chains 用 key；prerequisites.yaml 的 account_capabilities 用 alias（没有 key/code
# 字段——用 alias 标识测试账户 pt/xy），instrument_properties/known_codes 用 code。
# upsert() 对声明在此的 section 一律按此字段匹配；未声明的 section 才退回 entry 自带的
# key/code 猜测，且 entry 两者都没有时 fail closed（抛错），不静默按 None 匹配从而
# 误覆盖第一条记录（见国金 account_capabilities 的复审 repro）。
SECTION_ID_FIELD = {
    "entries": "key",
    "capabilities": "key",
    "verified_chains": "key",
    "account_capabilities": "alias",
    "instrument_properties": "code",
    "known_codes": "code",
}


def upsert(doc, section, entry, id_field=None):
    """在 doc[section]（list[dict]）内按标识字段做 upsert：
    命中已有条目则用 entry 的字段更新（last_verified/evidence_run 等），不新增重复；
    不存在则追加。原地修改 doc[section] 并返回 doc。

    标识字段解析顺序：
    1) 显式传入的 id_field 参数（调用方明确指定，优先级最高）；
    2) SECTION_ID_FIELD 里按 section 声明的标识字段（如 account_capabilities→alias）；
    3) 都没有时，退回旧行为：entry 自带 'key' 则用 'key'，否则 'code'；
    4) entry 既无 'key' 也无 'code' 且 section 未声明——fail closed：抛 ValueError，
       不静默按 `item.get('code') == None` 匹配（那会把第一条记录当成匹配项，导致跨记录
       字段互相污染，例如把 pt 账户的 mask/type 混进本该更新的 xy 账户）。

    entry 里若不含所选标识字段的值，同样 fail closed 抛错——upsert 必须知道匹配哪条记录。

    ⚠ 浅合并语义：命中已有条目时用 dict.update(entry) 逐键覆盖，**嵌套 dict 字段（如
    known_codes 的 attributes）会被 entry 里的同名值整块替换，而非深合并**。因此部分反哺
    某条记录的 attributes 时，caller 必须传**全量 attributes**（把不变的键也带上），否则未带
    的键会丢失。见 test_upsert_shallow_replaces_nested_attributes_caller_must_pass_full。
    """
    items = doc.setdefault(section, [])
    field = id_field or SECTION_ID_FIELD.get(section)
    if field is None:
        if "key" in entry:
            field = "key"
        elif "code" in entry:
            field = "code"
        else:
            raise ValueError(
                f"upsert: section={section!r} 未声明标识字段（见 SECTION_ID_FIELD），"
                f"entry 也既无 'key' 也无 'code'：{entry!r}。"
                "请显式传 id_field= 参数（fail closed，防止误覆盖用其它字段标识的记录，"
                "如 prerequisites.account_capabilities 用 alias）。"
            )
    if field not in entry:
        raise ValueError(
            f"upsert: entry 缺少标识字段 {field!r}（section={section!r}）：{entry!r}"
        )
    ident = entry[field]
    for item in items:
        if item.get(field) == ident:
            item.update(entry)
            return doc
    items.append(dict(entry))
    return doc


def _write_yaml(path, doc):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)


def reback_run(profile_path, prereq_path, results):
    """把一轮结果写回 profile.yaml / prerequisites.yaml。
    results 形如 {"profile": {section: [entry, ...], ...}, "prerequisites": {section: [entry, ...], ...}}，
    section 为对应 schema 的顶层数组字段名（如 entries/capabilities/verified_chains、
    account_capabilities/instrument_properties/known_codes）。
    写盘前分别用 profile/prerequisites schema 校验合并后的文档；任一校验失败则抛错、两个文件都不写。"""
    with open(profile_path, encoding="utf-8") as f:
        profile_doc = yaml.safe_load(f)
    with open(prereq_path, encoding="utf-8") as f:
        prereq_doc = yaml.safe_load(f)

    for section, entries in results.get("profile", {}).items():
        for entry in entries:
            upsert(profile_doc, section, entry)
    for section, entries in results.get("prerequisites", {}).items():
        for entry in entries:
            upsert(prereq_doc, section, entry)

    profile_errs = validate(profile_doc, "profile")
    prereq_errs = validate(prereq_doc, "prerequisites")
    if profile_errs or prereq_errs:
        raise ValueError(
            f"reback_run: schema 校验失败，未写盘。profile_errs={profile_errs} prereq_errs={prereq_errs}"
        )

    _write_yaml(profile_path, profile_doc)
    _write_yaml(prereq_path, prereq_doc)
